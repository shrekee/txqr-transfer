package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func opts(out string) Options {
	return Options{Out: out, Preset: "conservative", ECC: "M", Compression: "none", ChunkSize: 8, Scale: 2, FPS: 2}
}
func mustRead(t *testing.T, p string) []byte {
	t.Helper()
	b, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func getManifest(t *testing.T, p string) Manifest {
	t.Helper()
	var m Manifest
	if e := json.Unmarshal(mustRead(t, filepath.Join(p, "manifest.json")), &m); e != nil {
		t.Fatal(e)
	}
	return m
}
func TestGolden(t *testing.T) {
	var golden struct {
		Fixtures []struct {
			Metadata Metadata `json:"metadata"`
			Session  string   `json:"session"`
			Raw      string   `json:"raw_base64url"`
			Payloads []string `json:"payloads"`
		} `json:"fixtures"`
	}
	if e := json.Unmarshal(mustRead(t, "../protocol/golden.json"), &golden); e != nil {
		t.Fatal(e)
	}
	if len(golden.Fixtures) < 2 {
		t.Fatal("missing fixtures")
	}
	for _, g := range golden.Fixtures {
		if e := validateMetadata(g.Metadata); e != nil {
			t.Fatal(e)
		}
		if session(g.Metadata) != g.Session {
			t.Fatal("session mismatch")
		}
		raw, e := base64.RawURLEncoding.DecodeString(g.Raw)
		if e != nil {
			t.Fatal(e)
		}
		for i, want := range g.Payloads {
			start := i * g.Metadata.ChunkSize
			end := min(start+g.Metadata.ChunkSize, len(raw))
			got, e := frame(g.Metadata, i, raw[start:end])
			if e != nil || got != want {
				t.Fatalf("golden frame %d mismatch: %v", i, e)
			}
			if e = validateFrame([]byte(got), g.Metadata, i); e != nil {
				t.Fatal(e)
			}
			if e = validateFrame([]byte(strings.Replace(got, "TXQRT1:", "OTHER1:", 1)), g.Metadata, i); e == nil {
				t.Fatal("accepted unsupported format")
			}
		}
	}
}
func TestCanonicalASCII(t *testing.T) {
	b, e := canonical(map[string]any{"z": "<>&\"", "a": 1})
	if e != nil {
		t.Fatal(e)
	}
	if string(b) != `{"a":1,"z":"<>&\""}` {
		t.Fatalf("unexpected canonical JSON %s", b)
	}
	for _, name := range []string{"<>&.bin", "a\"b.bin", " x", ".hidden"} {
		if !safeName(name) {
			t.Fatal(name)
		}
	}
	for _, name := range []string{"", ".", "..", "a/b", "a\\b", "å", "x\n", "C:foo", "foo.", "foo ", "CON", "prn.txt", "COM1.bin", "LPT9", "AUX", "NUL", "CON .bin", "com1 .txt"} {
		if safeName(name) {
			t.Fatal(name)
		}
	}
}
func TestPresetPrecedence(t *testing.T) {
	o := Options{Preset: "fast", ChunkSize: 17, ECC: "H", FPS: 3, Scale: 9}
	if e := applyPreset(&o, map[string]bool{"chunk-size": true, "ecc": true}); e != nil {
		t.Fatal(e)
	}
	if o.ChunkSize != 17 || o.ECC != "H" || o.FPS != 8 || o.Scale != 4 {
		t.Fatalf("wrong precedence: %+v", o)
	}
	o.Preset = "unknown"
	if applyPreset(&o, nil) == nil {
		t.Fatal("accepted unknown preset")
	}
}
func TestCapacityAndSizing(t *testing.T) {
	o := opts("unused")
	o.QRVersion = 1
	o.Pixels = 8192
	if _, _, _, e := render(strings.Repeat("x", 1000), o); e == nil || !strings.Contains(e.Error(), "capacity") {
		t.Fatalf("pixels bypassed capacity: %v", e)
	}
	o.QRVersion = 0
	o.Pixels = 1
	if _, _, _, e := render("TXQRT1:test", o); e == nil {
		t.Fatal("undersized pixels accepted")
	}
	o.Pixels = 0
	if p, v, px, e := render("TXQRT1:test", o); e != nil || len(p) == 0 || v < 1 || px < 21 {
		t.Fatalf("render failed: %v", e)
	}
}
func TestSendReplayImmutable(t *testing.T) {
	d := t.TempDir()
	in := filepath.Join(d, "hello.bin")
	raw := []byte("hello, independent public sender\x00\xff")
	if e := os.WriteFile(in, raw, 0600); e != nil {
		t.Fatal(e)
	}
	out := filepath.Join(d, "send")
	if e := build(in, opts(out)); e != nil {
		t.Fatal(e)
	}
	m := getManifest(t, out)
	if len(m.Entries) != 5 {
		t.Fatal(len(m.Entries))
	}
	re := filepath.Join(d, "replay")
	if e := replay(out, re, "1,3"); e != nil {
		t.Fatal(e)
	}
	r := getManifest(t, re)
	if r.Session != m.Session || r.Metadata != m.Metadata || len(r.Entries) != 2 {
		t.Fatal("replay changed metadata")
	}
	for _, entry := range r.Entries {
		for _, path := range []string{entry.Payload, entry.QR} {
			if !bytes.Equal(mustRead(t, filepath.Join(out, path)), mustRead(t, filepath.Join(re, path))) {
				t.Fatal("replay changed source bytes")
			}
		}
	}
	html := string(mustRead(t, filepath.Join(re, "player.html")))
	if strings.Contains(html, "https:") || !strings.Contains(html, "data:image/png;base64,") {
		t.Fatal("player not standalone")
	}
	before := mustRead(t, filepath.Join(out, "manifest.json"))
	if e := build(in, opts(out)); e == nil {
		t.Fatal("overwrote existing directory")
	}
	if !bytes.Equal(before, mustRead(t, filepath.Join(out, "manifest.json"))) {
		t.Fatal("existing output changed")
	}
	if e := replay(out, filepath.Join(d, "bad"), "99"); e == nil {
		t.Fatal("accepted bad index")
	}
	report := filepath.Join(d, "missing.json")
	os.WriteFile(report, []byte(`{"session":"wrong","missing":[0]}`), 0600)
	if e := replay(out, filepath.Join(d, "bad2"), report); e == nil {
		t.Fatal("accepted mismatched report")
	}
	entry := m.Entries[0]
	os.WriteFile(filepath.Join(out, entry.Payload), []byte("corrupt"), 0600)
	if e := replay(out, filepath.Join(d, "bad3"), "0"); e == nil {
		t.Fatal("accepted corrupt payload")
	}
	if _, e := os.Stat(filepath.Join(d, "bad3")); !os.IsNotExist(e) {
		t.Fatal("failed replay output was not removed")
	}
}
func TestGzipDeterministicAndEmpty(t *testing.T) {
	d := t.TempDir()
	in := filepath.Join(d, "empty.bin")
	os.WriteFile(in, nil, 0600)
	o := opts(filepath.Join(d, "empty"))
	if e := build(in, o); e != nil {
		t.Fatal(e)
	}
	m := getManifest(t, o.Out)
	if m.Metadata.Chunks != 1 || m.Metadata.WireSize != 0 {
		t.Fatal("empty transfer")
	}
	o.Compression = "gzip"
	o.Out = filepath.Join(d, "gzip1")
	if e := build(in, o); e != nil {
		t.Fatal(e)
	}
	one := getManifest(t, o.Out)
	o.Out = filepath.Join(d, "gzip2")
	if e := build(in, o); e != nil {
		t.Fatal(e)
	}
	two := getManifest(t, o.Out)
	if one.Session != two.Session || one.Metadata != two.Metadata {
		t.Fatal("gzip not deterministic")
	}
}
func TestCLIInterspersed(t *testing.T) {
	d := t.TempDir()
	in := filepath.Join(d, "input.bin")
	os.WriteFile(in, []byte("cli"), 0600)
	out := filepath.Join(d, "out")
	if e := run([]string{"send", in, "--out", out, "--preset", "fast", "--chunk-size", "4", "--ecc", "M", "--scale", "2"}); e != nil {
		t.Fatal(e)
	}
	m := getManifest(t, out)
	if m.Metadata.ChunkSize != 4 || m.ECC != "M" || m.FPS != 8 {
		t.Fatal("CLI precedence")
	}
}
func TestMetadataBounds(t *testing.T) {
	m := Metadata{V: 1, Name: "x", RawSHA256: digest(nil), WireSHA256: digest(nil), Encoding: "none", ChunkSize: 1, Chunks: 1}
	if e := validateMetadata(m); e != nil {
		t.Fatal(e)
	}
	m.WireSize = 100001
	m.RawSize = 100001
	m.Chunks = 100001
	if validateMetadata(m) == nil {
		t.Fatal("accepted too many chunks")
	}
}
