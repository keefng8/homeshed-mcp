# Third-party licences

HomeShed's own code is licensed under [Apache-2.0](LICENSE). This page lists what it depends on, the code it
adapts from other open-source projects, and the optional services it can connect to.

## Python dependencies (installed with pip)

| Component | Licence | How it's used |
|---|---|---|
| mcp | MIT | MCP protocol server |
| uvicorn | BSD-3-Clause | ASGI web server |
| docker | Apache-2.0 | Docker tools |
| httpx | BSD-3-Clause | HTTP client |
| graphifyy | Apache-2.0 | Code-graph queries |
| z3-solver | MIT | Constraint solving for `reasoning.solve` |
| cryptography | Apache-2.0 OR BSD-3-Clause | Encrypting stored credentials (vault) |
| fastapi | MIT | The optional Control Panel (the `panel` extra) |
| tzdata | Apache-2.0 | Time-zone data (the `timezones` extra; the Docker image installs it) |

## Fonts in the optional Control Panel

The panel serves these itself; it never loads fonts from another site. Each licence file sits next to its font.

| Font | File | Licence | Copyright |
|---|---|---|---|
| [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | `panel/static/fonts/space-grotesk-latin-wght.woff2` | [SIL OFL 1.1](panel/static/fonts/SpaceGrotesk-OFL.txt) | 2020 The Space Grotesk Project Authors |
| [Orbitron](https://github.com/theleagueof/orbitron) | `panel/static/fonts/orbitron-wght.ttf` (the upstream file, unchanged) | [SIL OFL 1.1](panel/static/fonts/Orbitron-OFL.txt) | 2018 The Orbitron Project Authors (Reserved Font Name "Orbitron") |

## Code adapted from other projects

| Our file | Adapted from | Licence | What we changed |
|---|---|---|---|
| `tools/local_ai/classify_complexity.py` | [Lynkr](https://github.com/Fast-Editor/Lynkr), `native/src/lib.rs` (`analyze_complexity_native`). Copyright 2024-2025 Vishal Veera Reddy | Apache-2.0 | Ported from Rust to Python; added three broader force-cloud patterns |
| `tools/web/read.py` | [Agent-Reach](https://github.com/Panniantong/Agent-Reach), `channels/web.py` and `utils/url.py`. Copyright (c) 2025 Agent Eyes | MIT | Ported; the public-URL checks kept close to the original, the rest rewritten in this project's error-handling style |
| `tools/memory/recall_relevant.py` | [cognitive-workspace](https://github.com/tao-hpu/cognitive-workspace), the keyword-overlap relevance check (`_check_working_memory`). Copyright (c) 2025 Tao An | MIT | The approach only, applied to stored memory history; not its buffer system |
| `tools/observe/` (file format) | [task-observer](https://github.com/rebelytics/one-skill-to-rule-them-all) by Eoghan Henn | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | The observation file format and the "second violation → mechanical guard" rule, adapted as a tool |

Lynkr is under the same Apache-2.0 licence as HomeShed; its text is in [LICENSE](LICENSE). The MIT licence for
Agent-Reach and cognitive-workspace:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Optional backends (you install them yourself; nothing here is bundled)

| Component | Licence | How it's used |
|---|---|---|
| memory-core (TencentDB-Agent-Memory) | MIT | Persistent memory store |

## Online service

`web.read` fetches pages through [Jina Reader](https://jina.ai/reader/): the URL you ask for is sent to Jina.

Licences were checked against each project's LICENSE file or package metadata on 2026-09-29, and are re-checked
with `pip-licenses` for every release. If you spot an error, please open an issue.
