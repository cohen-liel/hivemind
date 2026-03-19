# create-hivemind

The fastest way to set up [Hivemind](https://github.com/cohen-liel/hivemind) — the open-source AI engineering team.

## Usage

```bash
npx create-hivemind@latest
```

That's it. The wizard handles everything: cloning, installing, building, and configuring.

## Options

```bash
# Non-interactive mode (accept all defaults)
npx create-hivemind@latest --yes

# Specify install directory
npx create-hivemind@latest my-hivemind

# Combine both
npx create-hivemind@latest my-hivemind --yes
```

## What it does

1. Checks your system (Node.js, Python, Git, Claude Code CLI)
2. Clones the Hivemind repository
3. Installs Python and Node.js dependencies
4. Builds the React frontend
5. Configures your `.env` file
6. Optionally starts the server

## Requirements

| Dependency | Version | Required |
|---|---|---|
| Node.js | 18+ | Yes |
| Python | 3.11+ | Yes |
| Git | Any | Yes |
| Claude Code CLI | Latest | Yes |
| Docker | Any | Optional |

## License

Apache-2.0
