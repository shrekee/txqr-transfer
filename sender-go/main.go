// Command txqr-go creates self-contained, offline TXQRT1 QR file transfers.
package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	qrcode "github.com/skip2/go-qrcode"
)

const magic = "TXQRT1:"
const maxBytes = 64 * 1024 * 1024
const maxFrame = 16384

type Metadata struct {
	V          int    `json:"v"`
	Name       string `json:"name"`
	RawSize    int    `json:"raw_size"`
	RawSHA256  string `json:"raw_sha256"`
	Encoding   string `json:"encoding"`
	WireSize   int    `json:"wire_size"`
	WireSHA256 string `json:"wire_sha256"`
	ChunkSize  int    `json:"chunk_size"`
	Chunks     int    `json:"chunks"`
}

func (m Metadata) object() map[string]any {
	return map[string]any{"v": m.V, "name": m.Name, "raw_size": m.RawSize, "raw_sha256": m.RawSHA256, "encoding": m.Encoding, "wire_size": m.WireSize, "wire_sha256": m.WireSHA256, "chunk_size": m.ChunkSize, "chunks": m.Chunks}
}

type Entry struct {
	Index         int    `json:"index"`
	Payload       string `json:"payload"`
	QR            string `json:"qr"`
	PayloadSHA256 string `json:"payload_sha256"`
	QRSHA256      string `json:"qr_sha256"`
	QRVersion     int    `json:"qr_version"`
	Pixels        int    `json:"pixels"`
}
type Manifest struct {
	Format   string   `json:"format"`
	Session  string   `json:"session"`
	Metadata Metadata `json:"metadata"`
	FPS      float64  `json:"fps"`
	ECC      string   `json:"ecc"`
	Entries  []Entry  `json:"payloads"`
}
type Options struct {
	Out, Preset, ECC, Compression, Name string
	ChunkSize, QRVersion, Pixels, Scale int
	FPS                                 float64
}

func canonical(v any) ([]byte, error) {
	var b bytes.Buffer
	e := json.NewEncoder(&b)
	e.SetEscapeHTML(false)
	if err := e.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(b.Bytes(), []byte("\n")), nil
}
func digest(b []byte) string    { d := sha256.Sum256(b); return hex.EncodeToString(d[:]) }
func session(m Metadata) string { b, _ := canonical(m.object()); return digest(b) }
func safeName(n string) bool {
	if len(n) < 1 || len(n) > 128 || n == "." || n == ".." || strings.HasSuffix(n, ".") || strings.HasSuffix(n, " ") {
		return false
	}
	for _, c := range []byte(n) {
		if c < 32 || c > 126 || c == '/' || c == '\\' || c == ':' {
			return false
		}
	}
	stem := strings.ToUpper(strings.TrimRight(strings.SplitN(n, ".", 2)[0], " "))
	if stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" || (len(stem) == 4 && (strings.HasPrefix(stem, "COM") || strings.HasPrefix(stem, "LPT")) && stem[3] >= '1' && stem[3] <= '9') {
		return false
	}
	return true
}
func validateMetadata(m Metadata) error {
	if m.V != 1 || !safeName(m.Name) || m.RawSize < 0 || m.RawSize > maxBytes || m.WireSize < 0 || m.WireSize > maxBytes || m.ChunkSize < 1 || m.ChunkSize > 4096 {
		return errors.New("invalid metadata bounds or filename")
	}
	for _, h := range []string{m.RawSHA256, m.WireSHA256} {
		b, e := hex.DecodeString(h)
		if e != nil || len(b) != 32 || strings.ToLower(h) != h {
			return errors.New("invalid metadata SHA-256")
		}
	}
	n := max(1, (m.WireSize+m.ChunkSize-1)/m.ChunkSize)
	if n > 100000 {
		return errors.New("transfer exceeds 100000 frames; increase --chunk-size")
	}
	if m.Chunks != n {
		return errors.New("invalid chunk count")
	}
	if m.Encoding != "none" && m.Encoding != "gzip" {
		return errors.New("unsupported encoding")
	}
	if m.Encoding == "none" && (m.RawSize != m.WireSize || m.RawSHA256 != m.WireSHA256) {
		return errors.New("uncompressed raw/wire mismatch")
	}
	return nil
}
func frame(m Metadata, i int, data []byte) (string, error) {
	b, e := canonical(map[string]any{"d": base64.RawURLEncoding.EncodeToString(data), "h": digest(data), "i": i, "m": m.object(), "s": session(m)})
	if e != nil {
		return "", e
	}
	s := magic + base64.RawURLEncoding.EncodeToString(b)
	if len(s) > maxFrame {
		return "", fmt.Errorf("frame %d exceeds %d-character protocol limit; reduce --chunk-size", i, maxFrame)
	}
	return s, nil
}
func applyPreset(o *Options, explicit map[string]bool) error {
	p := map[string]Options{"conservative": {ChunkSize: 256, ECC: "M", FPS: 2, Scale: 6}, "balanced": {ChunkSize: 512, ECC: "M", FPS: 4, Scale: 5}, "fast": {ChunkSize: 768, ECC: "L", FPS: 8, Scale: 4}}
	v, ok := p[o.Preset]
	if !ok {
		return fmt.Errorf("unknown preset %q (choose conservative, balanced, fast)", o.Preset)
	}
	if !explicit["chunk-size"] {
		o.ChunkSize = v.ChunkSize
	}
	if !explicit["ecc"] {
		o.ECC = v.ECC
	}
	if !explicit["fps"] {
		o.FPS = v.FPS
	}
	if !explicit["scale"] {
		o.Scale = v.Scale
	}
	return nil
}
func validateOptions(o Options) error {
	if o.Out == "" {
		return errors.New("--out is required")
	}
	if o.ChunkSize < 1 || o.ChunkSize > 4096 {
		return errors.New("--chunk-size must be 1..4096")
	}
	if o.QRVersion < 0 || o.QRVersion > 40 {
		return errors.New("--qr-version must be 0 (auto) or 1..40")
	}
	if o.Scale < 1 || o.Scale > 32 {
		return errors.New("--scale must be 1..32")
	}
	if o.Pixels < 0 || o.Pixels > 8192 {
		return errors.New("--pixels must be 0 (use scale) or 1..8192")
	}
	if math.IsNaN(o.FPS) || math.IsInf(o.FPS, 0) || o.FPS <= 0 || o.FPS > 60 {
		return errors.New("--fps must be greater than 0 and at most 60")
	}
	if _, e := eccLevel(o.ECC); e != nil {
		return e
	}
	if o.Compression != "none" && o.Compression != "gzip" {
		return errors.New("--compression must be none or gzip")
	}
	return nil
}
func eccLevel(s string) (qrcode.RecoveryLevel, error) {
	switch s {
	case "L":
		return qrcode.Low, nil
	case "M":
		return qrcode.Medium, nil
	case "Q":
		return qrcode.High, nil
	case "H":
		return qrcode.Highest, nil
	}
	return 0, errors.New("--ecc must be L, M, Q or H")
}
func render(payload string, o Options) ([]byte, int, int, error) {
	level, _ := eccLevel(o.ECC)
	var q *qrcode.QRCode
	var e error
	if o.QRVersion == 0 {
		q, e = qrcode.New(payload, level)
	} else {
		q, e = qrcode.NewWithForcedVersion(payload, o.QRVersion, level)
	}
	if e != nil {
		return nil, 0, 0, fmt.Errorf("QR capacity exceeded at ECC %s/version %d: %w; reduce --chunk-size, lower --ecc, or raise --qr-version (pixels do not increase capacity)", o.ECC, o.QRVersion, e)
	}
	modules := len(q.Bitmap())
	pixels := modules * o.Scale
	if o.Pixels > 0 {
		pixels = o.Pixels
		if pixels < modules {
			return nil, 0, 0, fmt.Errorf("--pixels %d is smaller than QR width %d modules including quiet zone", pixels, modules)
		}
		if pixels%modules != 0 {
			return nil, 0, 0, fmt.Errorf("--pixels %d must be an integer multiple of QR width %d; use --scale for crisp modules", pixels, modules)
		}
	}
	if pixels > 8192 {
		return nil, 0, 0, errors.New("rendered QR exceeds 8192 pixels; reduce --scale")
	}
	png, e := q.PNG(pixels)
	return png, q.VersionNumber, pixels, e
}
func reserveOutput(path string) (func(), error) {
	if e := os.Mkdir(path, 0755); e != nil {
		return nil, fmt.Errorf("create output (must not already exist): %w", e)
	}
	return func() { _ = os.RemoveAll(path) }, nil
}
func write(path string, b []byte) error { return os.WriteFile(path, b, 0644) }
func build(input string, o Options) (err error) {
	if e := validateOptions(o); e != nil {
		return e
	}
	f, e := os.Open(input)
	if e != nil {
		return e
	}
	defer f.Close()
	st, e := f.Stat()
	if e != nil {
		return e
	}
	if !st.Mode().IsRegular() {
		return errors.New("input must be a regular file")
	}
	if st.Size() > maxBytes {
		return errors.New("input exceeds 64 MiB")
	}
	raw, e := io.ReadAll(io.LimitReader(f, maxBytes+1))
	if e != nil {
		return e
	}
	if len(raw) > maxBytes {
		return errors.New("input exceeds 64 MiB")
	}
	name := o.Name
	if name == "" {
		name = filepath.Base(input)
	}
	if !safeName(name) {
		return errors.New("filename must be a safe printable ASCII basename of 1..128 bytes (no paths, colon, trailing dot/space or Windows device names); use --name")
	}
	wire := raw
	if o.Compression == "gzip" {
		var b bytes.Buffer
		z := gzip.NewWriter(&b)
		z.Header.ModTime = time.Time{}
		z.Header.OS = 255
		if _, e = z.Write(raw); e != nil {
			return e
		}
		if e = z.Close(); e != nil {
			return e
		}
		wire = b.Bytes()
	}
	if len(wire) > maxBytes {
		return errors.New("compressed data exceeds 64 MiB; use --compression none")
	}
	m := Metadata{V: 1, Name: name, RawSize: len(raw), RawSHA256: digest(raw), Encoding: o.Compression, WireSize: len(wire), WireSHA256: digest(wire), ChunkSize: o.ChunkSize, Chunks: max(1, (len(wire)+o.ChunkSize-1)/o.ChunkSize)}
	if e = validateMetadata(m); e != nil {
		return e
	}
	clean, e := reserveOutput(o.Out)
	if e != nil {
		return e
	}
	defer func() {
		if err != nil {
			clean()
		}
	}()
	for _, d := range []string{"payloads", "qr"} {
		if e = os.Mkdir(filepath.Join(o.Out, d), 0755); e != nil {
			return e
		}
	}
	man := Manifest{Format: "TXQRT1", Session: session(m), Metadata: m, FPS: o.FPS, ECC: o.ECC, Entries: make([]Entry, 0, m.Chunks)}
	for i := 0; i < m.Chunks; i++ {
		start := i * o.ChunkSize
		end := min(start+o.ChunkSize, len(wire))
		p, e := frame(m, i, wire[start:end])
		if e != nil {
			return e
		}
		png, v, px, e := render(p, o)
		if e != nil {
			return fmt.Errorf("frame %d: %w", i, e)
		}
		entry := Entry{Index: i, Payload: fmt.Sprintf("payloads/%06d.txt", i), QR: fmt.Sprintf("qr/%06d.png", i), PayloadSHA256: digest([]byte(p)), QRSHA256: digest(png), QRVersion: v, Pixels: px}
		if e = write(filepath.Join(o.Out, entry.Payload), []byte(p)); e != nil {
			return e
		}
		if e = write(filepath.Join(o.Out, entry.QR), png); e != nil {
			return e
		}
		man.Entries = append(man.Entries, entry)
	}
	if e = finishOutput(o.Out, man); e != nil {
		return e
	}
	fmt.Printf("Session %s\n%d frames; open %s\n", man.Session, m.Chunks, filepath.Join(o.Out, "player.html"))
	return nil
}
func finishOutput(out string, m Manifest) error {
	b, e := json.MarshalIndent(m, "", "  ")
	if e != nil {
		return e
	}
	if e = write(filepath.Join(out, "manifest.json"), append(b, '\n')); e != nil {
		return e
	}
	return writePlayer(out, m)
}
func writePlayer(out string, m Manifest) error {
	f, err := os.Create(filepath.Join(out, "player.html"))
	if err != nil {
		return err
	}
	defer f.Close()
	prefix := `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TXQR Transfer offline player</title><style>body{font:18px system-ui;background:#111;color:#eee;text-align:center;margin:16px}img{image-rendering:pixelated;max-width:min(85vw,80vh);height:auto;background:white}button,input{font:inherit;margin:8px}#info{overflow-wrap:anywhere;font-size:13px}</style><h1>TXQR Transfer</h1><img id="qr" alt="Transfer QR"><p id="count"></p><button id="prev">Previous</button><button id="play">Pause</button><button id="next">Next</button><label>FPS <input id="fps" type="number" min="0.1" max="60" step="0.1" value="` + strconv.FormatFloat(m.FPS, 'f', -1, 64) + `"></label><p id="info">Session ` + m.Session + `</p><script>"use strict";const frames=[`
	if _, err = io.WriteString(f, prefix); err != nil {
		return err
	}
	for i, entry := range m.Entries {
		if i > 0 {
			if _, err = io.WriteString(f, ","); err != nil {
				return err
			}
		}
		if _, err = fmt.Fprintf(f, `{"index":%d,"image":"data:image/png;base64,`, entry.Index); err != nil {
			return err
		}
		image, err := os.Open(filepath.Join(out, entry.QR))
		if err != nil {
			return err
		}
		enc := base64.NewEncoder(base64.StdEncoding, f)
		_, copyErr := io.Copy(enc, image)
		closeErr := image.Close()
		encErr := enc.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
		if encErr != nil {
			return encErr
		}
		if _, err = io.WriteString(f, `"}`); err != nil {
			return err
		}
	}
	suffix := `];let at=0,playing=true,timer;const qr=document.getElementById('qr'),count=document.getElementById('count'),fps=document.getElementById('fps'),play=document.getElementById('play');function show(){qr.src=frames[at].image;count.textContent='Frame '+frames[at].index+' — '+(at+1)+' / '+frames.length;}function restart(){clearInterval(timer);if(playing){const value=Number(fps.value);if(!Number.isFinite(value)||value<=0||value>60){fps.value=2;}timer=setInterval(()=>{at=(at+1)%frames.length;show();},1000/Number(fps.value));}}play.onclick=()=>{playing=!playing;play.textContent=playing?'Pause':'Play';restart();};document.getElementById('prev').onclick=()=>{at=(at+frames.length-1)%frames.length;show();};document.getElementById('next').onclick=()=>{at=(at+1)%frames.length;show();};fps.onchange=restart;show();restart();</script></html>`
	if _, err = io.WriteString(f, suffix); err != nil {
		return err
	}
	return f.Close()
}
func readBounded(path string, limit int64) ([]byte, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, limit+1))
	if e != nil {
		return nil, e
	}
	if int64(len(b)) > limit {
		return nil, fmt.Errorf("%s exceeds size limit", path)
	}
	return b, nil
}
func validateFrame(p []byte, m Metadata, index int) error {
	if len(p) > maxFrame || !bytes.HasPrefix(p, []byte(magic)) {
		return errors.New("invalid TXQRT1 payload prefix or length")
	}
	body, e := base64.RawURLEncoding.Strict().DecodeString(string(p[len(magic):]))
	if e != nil {
		return errors.New("invalid frame base64url")
	}
	var obj struct {
		D string   `json:"d"`
		H string   `json:"h"`
		I int      `json:"i"`
		M Metadata `json:"m"`
		S string   `json:"s"`
	}
	if e = json.Unmarshal(body, &obj); e != nil {
		return e
	}
	d, e := base64.RawURLEncoding.Strict().DecodeString(obj.D)
	if e != nil {
		return e
	}
	want := min(m.ChunkSize, m.WireSize-index*m.ChunkSize)
	if want < 0 || len(d) != want {
		return errors.New("invalid chunk length")
	}
	if obj.I != index || obj.S != session(m) || obj.H != digest(d) || obj.M != m {
		return errors.New("payload does not match manifest")
	}
	expected, e := frame(m, index, d)
	if e != nil {
		return e
	}
	if expected != string(p) {
		return errors.New("payload is not canonical")
	}
	return nil
}
func parseMissing(arg string, m Manifest) ([]int, error) {
	if arg == "" {
		ids := make([]int, len(m.Entries))
		for i, e := range m.Entries {
			ids[i] = e.Index
		}
		return ids, nil
	}
	var ids []int
	if _, e := os.Stat(arg); e == nil {
		b, e := readBounded(arg, 2*1024*1024)
		if e != nil {
			return nil, e
		}
		var report struct {
			Session string `json:"session"`
			Missing []int  `json:"missing"`
		}
		dec := json.NewDecoder(bytes.NewReader(b))
		if e = dec.Decode(&report); e != nil {
			return nil, fmt.Errorf("missing report: %w", e)
		}
		if e = dec.Decode(new(any)); e != io.EOF {
			return nil, errors.New("missing report contains trailing JSON or invalid data")
		}
		if report.Session != m.Session {
			return nil, errors.New("missing report session does not match transfer")
		}
		if report.Missing == nil {
			return nil, errors.New("missing report must include a missing array")
		}
		ids = report.Missing
	} else {
		for _, s := range strings.Split(arg, ",") {
			n, e := strconv.Atoi(strings.TrimSpace(s))
			if e != nil {
				return nil, fmt.Errorf("--missing must be a JSON report path or comma-separated indices: %w", e)
			}
			ids = append(ids, n)
		}
	}
	if len(ids) == 0 {
		return nil, errors.New("missing selection is empty; nothing to replay")
	}
	seen := map[int]bool{}
	for _, i := range ids {
		if i < 0 || i >= m.Metadata.Chunks {
			return nil, fmt.Errorf("missing index %d is out of range", i)
		}
		if seen[i] {
			return nil, fmt.Errorf("duplicate missing index %d", i)
		}
		seen[i] = true
	}
	sort.Ints(ids)
	return ids, nil
}
func replay(source, out, missing string) (err error) {
	if out == "" {
		return errors.New("--out is required")
	}
	st, e := os.Stat(source)
	if e != nil {
		return e
	}
	if st.IsDir() {
		source = filepath.Join(source, "manifest.json")
	}
	root := filepath.Dir(source)
	b, e := readBounded(source, 64*1024*1024)
	if e != nil {
		return e
	}
	var m Manifest
	if e = json.Unmarshal(b, &m); e != nil {
		return e
	}
	if m.Format != "TXQRT1" {
		return errors.New("unsupported manifest format")
	}
	if e = validateMetadata(m.Metadata); e != nil {
		return e
	}
	if m.Session != session(m.Metadata) {
		return errors.New("manifest session does not bind metadata")
	}
	if m.FPS <= 0 || m.FPS > 60 || math.IsNaN(m.FPS) {
		return errors.New("invalid manifest fps")
	}
	if len(m.Entries) == 0 || len(m.Entries) > m.Metadata.Chunks {
		return errors.New("invalid manifest payload count")
	}
	entries := map[int]Entry{}
	for _, entry := range m.Entries {
		if entry.Index < 0 || entry.Index >= m.Metadata.Chunks {
			return errors.New("invalid manifest index")
		}
		if _, ok := entries[entry.Index]; ok {
			return errors.New("duplicate manifest index")
		}
		if entry.Payload != fmt.Sprintf("payloads/%06d.txt", entry.Index) || entry.QR != fmt.Sprintf("qr/%06d.png", entry.Index) {
			return errors.New("unsafe manifest path")
		}
		entries[entry.Index] = entry
	}
	ids, e := parseMissing(missing, m)
	if e != nil {
		return e
	}
	selected := make([]Entry, 0, len(ids))
	for _, i := range ids {
		entry, ok := entries[i]
		if !ok {
			return fmt.Errorf("frame %d is not present in this source manifest", i)
		}
		selected = append(selected, entry)
	}
	clean, e := reserveOutput(out)
	if e != nil {
		return e
	}
	defer func() {
		if err != nil {
			clean()
		}
	}()
	for _, d := range []string{"payloads", "qr"} {
		if e = os.Mkdir(filepath.Join(out, d), 0755); e != nil {
			return e
		}
	}
	for _, entry := range selected {
		p, e := readBounded(filepath.Join(root, entry.Payload), maxFrame)
		if e != nil {
			return e
		}
		if digest(p) != entry.PayloadSHA256 {
			return errors.New("source payload hash mismatch")
		}
		if e = validateFrame(p, m.Metadata, entry.Index); e != nil {
			return e
		}
		png, e := readBounded(filepath.Join(root, entry.QR), 16*1024*1024)
		if e != nil {
			return e
		}
		if digest(png) != entry.QRSHA256 {
			return errors.New("source QR hash mismatch")
		}
		if e = write(filepath.Join(out, entry.Payload), p); e != nil {
			return e
		}
		if e = write(filepath.Join(out, entry.QR), png); e != nil {
			return e
		}
	}
	m.Entries = selected
	if e = finishOutput(out, m); e != nil {
		return e
	}
	fmt.Printf("Replayed %d original frames from session %s\nOpen %s\n", len(selected), m.Session, filepath.Join(out, "player.html"))
	return nil
}
func reorder(args []string) ([]string, error) {
	var flags, pos []string
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--" {
			pos = append(pos, args[i+1:]...)
			break
		}
		if strings.HasPrefix(a, "-") {
			flags = append(flags, a)
			if a != "-h" && a != "--help" && !strings.Contains(a, "=") {
				i++
				if i >= len(args) {
					return nil, fmt.Errorf("option %s needs a value", a)
				}
				flags = append(flags, args[i])
			}
		} else {
			pos = append(pos, a)
		}
	}
	return append(append(flags, "--"), pos...), nil
}
func run(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: txqr-go send INPUT --out DIR [options] | replay SOURCE --out DIR [--missing REPORT.json|0,2]")
	}
	if args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Println("txqr-go send INPUT --out DIR [--preset conservative|balanced|fast] [--chunk-size N] [--ecc L|M|Q|H] [--qr-version 0..40] [--scale N | --pixels N] [--fps N] [--compression none|gzip] [--name BASENAME]\ntxqr-go replay SOURCE --out DIR [--missing REPORT.json|0,2]\nOutputs are offline and output directories must not exist. Replay never repacks or regenerates QR codes.")
		return nil
	}
	a, e := reorder(args[1:])
	if e != nil {
		return e
	}
	fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
	switch args[0] {
	case "send":
		var o Options
		fs.StringVar(&o.Out, "out", "", "new output directory (required)")
		fs.StringVar(&o.Preset, "preset", "conservative", "conservative, balanced, fast (explicit options win)")
		fs.IntVar(&o.ChunkSize, "chunk-size", 256, "wire bytes per frame (1..4096)")
		fs.StringVar(&o.ECC, "ecc", "M", "QR error correction L, M, Q, H")
		fs.IntVar(&o.QRVersion, "qr-version", 0, "QR version 0=auto, otherwise 1..40")
		fs.IntVar(&o.Scale, "scale", 6, "integer pixels per module (1..32)")
		fs.IntVar(&o.Pixels, "pixels", 0, "exact square width; must be module multiple (0=scale)")
		fs.Float64Var(&o.FPS, "fps", 2, "offline player frames per second (0..60)")
		fs.StringVar(&o.Compression, "compression", "none", "none or deterministic gzip")
		fs.StringVar(&o.Name, "name", "", "override transmitted ASCII basename")
		if e = fs.Parse(a); e != nil {
			return e
		}
		if fs.NArg() != 1 {
			return errors.New("send requires exactly one INPUT")
		}
		explicit := map[string]bool{}
		fs.Visit(func(f *flag.Flag) { explicit[f.Name] = true })
		if explicit["scale"] && explicit["pixels"] {
			return errors.New("choose --scale or --pixels, not both")
		}
		if e = applyPreset(&o, explicit); e != nil {
			return e
		}
		return build(fs.Arg(0), o)
	case "replay":
		var out, missing string
		fs.StringVar(&out, "out", "", "new output directory (required)")
		fs.StringVar(&missing, "missing", "", "receiver JSON report or comma-separated zero-based indices")
		if e = fs.Parse(a); e != nil {
			return e
		}
		if fs.NArg() != 1 {
			return errors.New("replay requires exactly one source directory or manifest")
		}
		return replay(fs.Arg(0), out, missing)
	default:
		return fmt.Errorf("unknown command %q; use --help", args[0])
	}
}
func main() {
	if e := run(os.Args[1:]); e != nil {
		if errors.Is(e, flag.ErrHelp) {
			return
		}
		fmt.Fprintln(os.Stderr, "txqr-go:", e)
		os.Exit(1)
	}
}
