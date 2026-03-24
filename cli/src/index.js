import chalk from 'chalk';
import { execSync, spawn } from 'child_process';
import { existsSync, mkdirSync, writeFileSync, chmodSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import ora from 'ora';
import gradient from 'gradient-string';
import figlet from 'figlet';

const REPO_URL = 'https://github.com/cohen-liel/hivemind.git';
const DEFAULT_DIR = 'hivemind';
const VERSION = '1.2.0';

const hivemindGradient = gradient(['#6366f1', '#8b5cf6', '#a855f7']);

function printBanner() {
  console.log('');
  console.log(
    hivemindGradient(
      figlet.textSync('Hivemind', {
        font: 'ANSI Shadow',
        horizontalLayout: 'fitted',
      })
    )
  );
  console.log('');
  console.log(
    chalk.dim('  One prompt. A full AI engineering team. Go lie on the couch.')
  );
  console.log(
    chalk.dim('  ─────────────────────────────────────────────────────────────')
  );
  console.log('');
}

function printHelp() {
  printBanner();
  console.log(chalk.bold('  Usage:\n'));
  console.log('    npx create-hivemind@latest [directory]\n');
  console.log(chalk.bold('  Options:\n'));
  console.log('    -h, --help      Show this help message');
  console.log('    -v, --version   Show version number');
  console.log('');
  console.log(chalk.bold('  Examples:\n'));
  console.log(chalk.dim('    npx create-hivemind@latest'));
  console.log(chalk.dim('    npx create-hivemind@latest ~/my-hivemind'));
  console.log('');
  console.log(chalk.bold('  What happens:\n'));
  console.log(chalk.dim('    1. Clones the Hivemind repo'));
  console.log(chalk.dim('    2. Installs Python & Node dependencies'));
  console.log(chalk.dim('    3. Builds the frontend dashboard'));
  console.log(chalk.dim('    4. Installs Cloudflare Tunnel for remote access'));
  console.log(chalk.dim('    5. Starts the server and prints your access URL'));
  console.log('');
  console.log(chalk.bold('  Prerequisites:\n'));
  console.log(chalk.dim('    - Node.js 18+'));
  console.log(chalk.dim('    - Python 3.11+'));
  console.log(chalk.dim('    - Git'));
  console.log(chalk.dim('    - Claude Code CLI (npm i -g @anthropic-ai/claude-code)'));
  console.log('');
  console.log(chalk.bold('  After installation:\n'));
  console.log(chalk.dim('    cd hivemind && ./restart.sh    # restart the server'));
  console.log('');
}

function checkPrerequisites() {
  const checks = [];

  // Check Node.js version
  try {
    const nodeVersion = process.version;
    const major = parseInt(nodeVersion.slice(1).split('.')[0], 10);
    if (major >= 18) {
      checks.push({ name: 'Node.js', status: 'ok', detail: nodeVersion });
    } else {
      checks.push({
        name: 'Node.js',
        status: 'warn',
        detail: `${nodeVersion} (18+ recommended)`,
      });
    }
  } catch {
    checks.push({ name: 'Node.js', status: 'fail', detail: 'not found' });
  }

  // Check Python
  try {
    const pyVersion = execSync('python3 --version 2>&1', {
      encoding: 'utf-8',
    }).trim();
    checks.push({ name: 'Python', status: 'ok', detail: pyVersion });
  } catch {
    checks.push({ name: 'Python', status: 'fail', detail: 'not found — install Python 3.11+' });
  }

  // Check Git
  try {
    const gitVersion = execSync('git --version 2>&1', {
      encoding: 'utf-8',
    }).trim();
    checks.push({ name: 'Git', status: 'ok', detail: gitVersion });
  } catch {
    checks.push({ name: 'Git', status: 'fail', detail: 'not found' });
  }

  // Check Claude Code CLI (required for agents to work)
  try {
    const claudeVersion = execSync('claude --version 2>&1', {
      encoding: 'utf-8',
    }).trim();
    checks.push({
      name: 'Claude Code CLI',
      status: 'ok',
      detail: claudeVersion,
    });
  } catch {
    checks.push({
      name: 'Claude Code CLI',
      status: 'warn',
      detail: 'not found — install after setup: npm i -g @anthropic-ai/claude-code',
    });
  }

  return checks;
}

function printChecks(checks) {
  console.log(chalk.bold('  System Check\n'));

  for (const check of checks) {
    const icon =
      check.status === 'ok'
        ? chalk.green('  ✓')
        : check.status === 'warn'
          ? chalk.yellow('  ⚠')
          : check.status === 'info'
            ? chalk.blue('  ○')
            : chalk.red('  ✗');
    const detail = chalk.dim(check.detail);
    console.log(`${icon} ${chalk.white(check.name)}  ${detail}`);
  }
  console.log('');

  const hasFail = checks.some((c) => c.status === 'fail');
  if (hasFail) {
    console.log(
      chalk.red(
        '  Some required dependencies are missing. Please install them and try again.\n'
      )
    );
    return false;
  }

  // Warn about Claude Code CLI more prominently
  const claudeCheck = checks.find((c) => c.name === 'Claude Code CLI');
  if (claudeCheck && claudeCheck.status === 'warn') {
    console.log(
      chalk.yellow(
        '  ⚠  Claude Code CLI is required for AI agents to work.\n' +
        '     Install it after setup: npm i -g @anthropic-ai/claude-code\n' +
        '     Then run: claude login\n'
      )
    );
  }

  return true;
}

function runCommand(command, cwd, stdio = 'pipe') {
  return new Promise((resolve, reject) => {
    const child = spawn('sh', ['-c', command], {
      cwd,
      stdio,
      env: { ...process.env },
    });

    let stdout = '';
    let stderr = '';

    if (child.stdout) {
      child.stdout.on('data', (data) => {
        stdout += data.toString();
      });
    }
    if (child.stderr) {
      child.stderr.on('data', (data) => {
        stderr += data.toString();
      });
    }

    child.on('close', (code) => {
      if (code === 0) {
        resolve(stdout);
      } else {
        reject(new Error(`Command failed (exit ${code}): ${stderr || stdout}`));
      }
    });
  });
}

export async function main() {
  const args = process.argv.slice(2);

  // Handle --help and --version before anything else
  if (args.includes('--help') || args.includes('-h')) {
    printHelp();
    process.exit(0);
  }

  if (args.includes('--version') || args.includes('-v')) {
    console.log(`create-hivemind v${VERSION}`);
    process.exit(0);
  }

  // The only optional argument is the install directory
  const targetArg = args.find((a) => !a.startsWith('-'));
  const projectDir = targetArg || DEFAULT_DIR;
  const fullPath = resolve(process.cwd(), projectDir);
  const homeDir = process.env.HOME || '~';

  printBanner();

  // System check
  const checks = checkPrerequisites();
  const canProceed = printChecks(checks);
  if (!canProceed) {
    process.exit(1);
  }

  console.log(chalk.bold('  Installing to: ') + chalk.cyan(fullPath));
  console.log('');

  // Step 1: Clone
  const spinnerClone = ora({
    text: chalk.dim('Cloning Hivemind repository...'),
    color: 'magenta',
  }).start();

  try {
    if (existsSync(fullPath)) {
      spinnerClone.warn(chalk.yellow(`Directory ${projectDir} already exists. Pulling latest...`));
      await runCommand('git pull origin main', fullPath);
    } else {
      await runCommand(`git clone ${REPO_URL} "${fullPath}"`, process.cwd());
    }
    spinnerClone.succeed(chalk.green('Repository cloned'));
  } catch (err) {
    spinnerClone.fail(chalk.red('Failed to clone repository'));
    console.error(chalk.dim(err.message));
    process.exit(1);
  }

  // Step 2: Configure .env with sensible defaults (no questions asked)
  const spinnerEnv = ora({
    text: chalk.dim('Configuring environment...'),
    color: 'magenta',
  }).start();

  try {
    const envPath = join(fullPath, '.env');
    const envExamplePath = join(fullPath, '.env.example');

    if (existsSync(envExamplePath) && !existsSync(envPath)) {
      // Sensible defaults — user can edit .env later if needed
      const envContent = [
        `CLAUDE_PROJECTS_DIR=${homeDir}/hivemind-projects`,
        'DASHBOARD_HOST=0.0.0.0',
        'DASHBOARD_PORT=8080',
        'DEVICE_AUTH_ENABLED=true',
        '',
      ].join('\n');
      writeFileSync(envPath, envContent);
    }
    spinnerEnv.succeed(chalk.green('Environment configured'));
  } catch (err) {
    spinnerEnv.warn(chalk.yellow('Could not create .env — using defaults'));
  }

  // Step 3: Run setup.sh
  const spinnerSetup = ora({
    text: chalk.dim('Installing dependencies and building frontend (this may take a few minutes)...'),
    color: 'magenta',
  }).start();

  try {
    chmodSync(join(fullPath, 'setup.sh'), '755');
    chmodSync(join(fullPath, 'restart.sh'), '755');
    await runCommand('./setup.sh', fullPath);
    spinnerSetup.succeed(chalk.green('Dependencies installed and frontend built'));
  } catch (err) {
    spinnerSetup.fail(chalk.red('Setup failed'));
    console.error(chalk.dim(err.message));
    console.log('');
    console.log(chalk.dim('  Try running manually:'));
    console.log(chalk.dim(`  cd ${projectDir} && ./setup.sh`));
    console.log('');
    process.exit(1);
  }

  // Step 4: Start server automatically
  console.log('');
  console.log(chalk.bold('  Starting Hivemind...\n'));

  const child = spawn('sh', ['-c', './restart.sh --no-clear'], {
    cwd: fullPath,
    stdio: 'inherit',
    detached: false,
  });

  child.on('error', (err) => {
    console.error(chalk.red(`\n  Failed to start: ${err.message}`));
    printSuccess(fullPath);
  });
}

function printSuccess(fullPath) {
  const dirName = fullPath.split('/').pop();
  console.log('');
  console.log(
    hivemindGradient(
      '  ╔══════════════════════════════════════════════════════════╗'
    )
  );
  console.log(
    hivemindGradient(
      '  ║              Hivemind is installed!                      ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ╠══════════════════════════════════════════════════════════╣'
    )
  );
  console.log(
    hivemindGradient(
      '  ║                                                          ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ║  To start the server:                                    ║'
    )
  );
  console.log(
    hivemindGradient(
      `  ║    cd ${dirName} && ./restart.sh`.padEnd(60) + '║'
    )
  );
  console.log(
    hivemindGradient(
      '  ║                                                          ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ║  To customize settings:                                  ║'
    )
  );
  console.log(
    hivemindGradient(
      `  ║    nano ${dirName}/.env`.padEnd(60) + '║'
    )
  );
  console.log(
    hivemindGradient(
      '  ║                                                          ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ╚══════════════════════════════════════════════════════════╝'
    )
  );
  console.log('');
  console.log(chalk.dim('  Now go lie on the couch. Your team has got this.'));
  console.log('');
}
