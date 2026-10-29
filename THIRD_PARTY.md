# Third-party software

This repository declares dependencies rather than copying dependency implementation source. Released Go executables statically include go-qrcode; its complete MIT notice is retained below and included in the release archives. Dependency license terms remain applicable. When distributing built executables, Python environments, wheels, or containers, retain the complete relevant licenses and notices, including bundled transitive codec libraries.

| Dependency | Pinned version | Upstream license / notice |
| --- | --- | --- |
| [skip2/go-qrcode](https://github.com/skip2/go-qrcode) | `v0.0.0-20200617195104-da1b6568686e` | [MIT, Tom Harwood](https://github.com/skip2/go-qrcode/blob/master/LICENSE) |
| [zxing-cpp Python bindings](https://github.com/zxing-cpp/zxing-cpp/tree/v2.3.0/wrappers/python) | `2.3.0` | [Apache-2.0](https://github.com/zxing-cpp/zxing-cpp/blob/v2.3.0/LICENSE) |
| [opencv-python-headless](https://pypi.org/project/opencv-python-headless/4.14.0.94/) | `4.14.0.94` | Packaging MIT; OpenCV Apache-2.0; wheels include separate notices, including FFmpeg LGPL-2.1. See installed `LICENSE.txt` and `LICENSE-3RD-PARTY.txt`, and [upstream licenses](https://github.com/opencv/opencv-python#licensing). |
| [NumPy](https://numpy.org/) | `2.1.3` | [BSD-3-Clause plus bundled library notices](https://github.com/numpy/numpy/blob/v2.1.3/LICENSE.txt) |
| [pytest](https://docs.pytest.org/) (development only) | `8.3.4` | [MIT](https://github.com/pytest-dev/pytest/blob/8.3.4/LICENSE) |

QR encoding and recognition are provided by mature upstream libraries; this project implements the file-transfer envelope, storage and CLI coordination, not its own QR codec. The historic divan/txqr project is not a dependency, and its protocol is not implemented here.

Original code is licensed under the [MIT License](LICENSE). Third-party components retain their respective licenses.

## Complete go-qrcode MIT notice

Source: pinned module `github.com/skip2/go-qrcode@v0.0.0-20200617195104-da1b6568686e/LICENSE`.

```text
Copyright (c) 2014 Tom Harwood

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## Complete Go runtime and standard-library notice

Released Go executables include the Go runtime and standard library. The following license and patent grant are reproduced from the official Go 1.27.3 toolchain distribution.

### LICENSE

```text
Copyright 2009 The Go Authors.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * Neither the name of Google LLC nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### PATENTS

```text
Additional IP Rights Grant (Patents)

"This implementation" means the copyrightable works distributed by
Google as part of the Go project.

Google hereby grants to You a perpetual, worldwide, non-exclusive,
no-charge, royalty-free, irrevocable (except as stated in this section)
patent license to make, have made, use, offer to sell, sell, import,
transfer and otherwise run, modify and propagate the contents of this
implementation of Go, where such license applies only to those patent
claims, both currently owned or controlled by Google and acquired in
the future, licensable by Google that are necessarily infringed by this
implementation of Go.  This grant does not include claims that would be
infringed only as a consequence of further modification of this
implementation.  If you or your agent or exclusive licensee institute or
order or agree to the institution of patent litigation against any
entity (including a cross-claim or counterclaim in a lawsuit) alleging
that this implementation of Go or any code incorporated within this
implementation of Go constitutes direct or contributory patent
infringement, or inducement of patent infringement, then any patent
rights granted to you under this License for this implementation of Go
shall terminate as of the date such litigation is filed.
```
