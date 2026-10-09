# Demo GIF script

Goal: a first-time visitor understands "one command, and my AI gets real tools" in about 30 seconds.
Output: `docs/assets/demo.gif`, referenced from the README.

## Setup

- Terminal 100×28, dark theme, 16–18 px font, a clean prompt.
- **Nothing personal on screen:** no home paths, hostnames, IP addresses or tokens.
- Record with [vhs](https://github.com/charmbracelet/vhs) (scriptable, repeatable) or asciinema + agg, at 15 fps.
  Final GIF under 5 MB.
- **Real runs only, never staged output.** Use only tools that need no setup, so anyone can reproduce it.
- Typing at about 40 ms per character; pause 1.5 s on every result.

## Shots

1. **0:00–0:07. Install.** Type `uvx homeshed-mcp setup`. It reports the local model check and asks
   `Add homeshed-mcp to Claude Code now? [Y/n]`. Press Enter: Claude Code confirms it was added. Then type `claude`.
2. **0:07–0:18. Real tools.** In Claude Code, ask: *"Is port 443 open on example.com, and what are github.com's
   IP addresses?"* The `network.port_check` and `network.dns.lookup` calls appear, then a short answer.
3. **0:18–0:28. Something it couldn't do alone.** Ask: *"Use the logic solver: which whole numbers x and y give
   x + y = 10 and x − y = 4?"* The `reasoning.solve` call appears, then `x = 7, y = 3`.
4. **0:28–0:31. End card.** `HomeShed · every tool your AI needs · one line to install`, with
   `uvx homeshed-mcp setup` below it.

Re-record whenever the setup output or the tool names change.
