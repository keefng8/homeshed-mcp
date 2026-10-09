# Getting started with HomeShed

This guide takes you from nothing to a working setup, one step at a time. You don't need to know how to code.

**A few words first**

- **MCP** (Model Context Protocol) is a standard way for AI apps, like Claude or Cursor, to use tools.
  HomeShed is a set of tools your AI app can use.
- A **terminal** is a window where you type commands instead of clicking.
- **uv** is a small free program that downloads and runs HomeShed for you.

## Steps

1. **Open a terminal.**
   - Windows: press **Start**, type `PowerShell`, press **Enter**.
   - macOS: press **Cmd + Space**, type `Terminal`, press **Enter**.

   *You should see:* a window with a blinking cursor, waiting for you to type.

2. **Install uv.** Copy the line for your computer, paste it into the terminal, and press **Enter**.
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS or Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

   *You should see:* a message that uv was installed.

   Then **close the terminal and open a new one**, so it notices uv. In the new one, type `uv --version` and press
   **Enter**: a version number means it worked.

3. **Add HomeShed to your AI app.** It works with apps installed on your computer (the Claude website can't use it).
   - **Claude Code** (if you have it installed): paste `uvx homeshed-mcp setup` and press **Enter**. It checks for a local AI model, then shows
     a short list of optional tool groups (Docker, phone notifications and so on), with the ones it found already
     ticked. Press **Enter** to keep them; your AI only sees the groups you keep, which saves it time and money. Then
     it asks "Add homeshed-mcp to Claude Code now? [Y/n]". Press **Enter** (or type **Y**). It adds HomeShed using Claude
     Code's own command, and changes nothing else.
     Or add it yourself: paste `claude mcp add -s user homeshed-mcp -- uvx homeshed-mcp` and press **Enter**.
   - **Cursor or VS Code:** click the **Add to Cursor** or **Install in VS Code** button in the
     [README](../README.md#install).
   - **Claude Desktop:** click the **Claude Desktop** button in the [README](../README.md#install). It downloads
     `homeshed-mcp.mcpb` (on the [Releases page](https://github.com/keefng8/homeshed-mcp/releases/latest) it's under
     **Assets**; not "Source code"). Double-click that file.

   *You should see:* in Claude Code, a line saying homeshed-mcp was added; in Cursor or VS Code, homeshed-mcp in the
   app's MCP list; in Claude Desktop, its window asking to install the extension.

4. **Restart your AI app completely.** Quit it, not just close the window: on Windows, right-click the app's icon by
   the clock and choose **Quit**; on macOS, press **Cmd + Q**. Then open it again.

   *You should see:* the app start normally.

5. **Try it.** Type these into your AI app's chat, not the terminal (in Cursor and VS Code, use agent mode):
   - *"What tools do you have from homeshed-mcp?"*
   - *"What operating system and CPU is this machine running?"*

   *You should see:* the app use a homeshed-mcp tool (most apps show the tool's name, and may ask your permission
   first), then a list of tools and your computer's details. If the app says it has no such tools, see below.

## If something doesn't work

- **"... is not recognized as the name of a cmdlet"** (Windows) or **"command not found"** (macOS, Linux), naming
  `uv` or `uvx`: close the terminal, open a new one, and try again.
- **Behind a company network:** if downloads fail or mention certificates, set your proxy (`HTTPS_PROXY`), and
  set `UV_NATIVE_TLS=1` so uv trusts the certificates your computer already trusts. Then try again.
- **The tools don't show up:** quit your AI app completely and open it again. On macOS, an app opened from the Dock
  may not find `uvx`: in the terminal, type `which uvx`, and put the path it prints in the app's MCP settings in place
  of `uvx`.
- **Anything else:** run `uvx homeshed-mcp doctor`. It checks everything and tells you exactly what to fix. If you're
  still stuck, [open an issue](https://github.com/keefng8/homeshed-mcp/issues/new/choose). Never paste keys or passwords.

## Next steps

- **Use your own AI model** (for example Ollama) so routine work doesn't use your paid AI: see "Want more?" in the
  [README](../README.md#install).
- **Tools that change things** (git commits, Docker) are **off by default** for safety. Switch on only the ones you
  need, when you need them.
