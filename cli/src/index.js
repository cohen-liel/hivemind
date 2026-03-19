import chalk from 'chalk';
import { execSync, spawn } from 'child_process';
import { existsSync, mkdirSync, writeFileSync, chmodSync } from 'fs';
import { join, resolve } from 'path';
import inquirer from 'inquirer';
import ora from 'ora';
import gradient from 'gradient-string';
import figlet from 'figlet';

const REPO_URL = 'https://github.com/cohen-liel/hivemind.git';
const DEFAULT_DIR = 'hivemind';

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
    checks.push({ name: 'Python', status: 'fail', detail: 'not found' });
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

  // Check Claude Code CLI
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
      detail: 'not found — install with: npm i -g @anthropic-ai/claude-code',
    });
  }

  // Check Docker (optional)
  try {
    const dockerVersion = execSync('docker --version 2>&1', {
      encoding: 'utf-8',
    }).trim();
    checks.push({
      name: 'Docker',
      status: 'ok',
      detail: `${dockerVersion} (optional)`,
    });
  } catch {
    checks.push({
      name: 'Docker',
      status: 'info',
      detail: 'not found (optional)',
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
  const isYes = args.includes('--yes') || args.includes('-y');
  const targetArg = args.find((a) => !a.startsWith('-'));

  printBanner();

  // System check
  const checks = checkPrerequisites();
  const canProceed = printChecks(checks);
  if (!canProceed) {
    process.exit(1);
  }

  let projectDir;
  let projectsDir;
  let installMethod;
  let startServer;

  if (isYes) {
    // Non-interactive mode
    projectDir = targetArg || DEFAULT_DIR;
    projectsDir = resolve(process.env.HOME || '~', 'projects');
    installMethod = 'auto';
    startServer = true;
  } else {
    // Interactive wizard
    console.log(chalk.bold('  Setup Wizard\n'));

    const answers = await inquirer.prompt([
      {
        type: 'input',
        name: 'projectDir',
        message: 'Where should we install Hivemind?',
        default: targetArg || DEFAULT_DIR,
      },
      {
        type: 'input',
        name: 'projectsDir',
        message: 'Where are your code projects? (CLAUDE_PROJECTS_DIR)',
        default: resolve(process.env.HOME || '~', 'projects'),
      },
      {
        type: 'list',
        name: 'installMethod',
        message: 'Installation method:',
        choices: [
          { name: '🚀 Auto (recommended) — clone, install, build, configure', value: 'auto' },
          { name: '🐳 Docker — docker-compose up', value: 'docker' },
          { name: '📋 Manual — just clone, I\'ll handle the rest', value: 'manual' },
        ],
        default: 'auto',
      },
      {
        type: 'confirm',
        name: 'startServer',
        message: 'Start Hivemind after installation?',
        default: true,
        when: (a) => a.installMethod !== 'manual',
      },
    ]);

    projectDir = answers.projectDir;
    projectsDir = answers.projectsDir;
    installMethod = answers.installMethod;
    startServer = answers.startServer ?? false;
  }

  const fullPath = resolve(process.cwd(), projectDir);

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

  if (installMethod === 'manual') {
    console.log('');
    console.log(chalk.bold('  Next steps:\n'));
    console.log(chalk.dim(`  cd ${projectDir}`));
    console.log(chalk.dim('  chmod +x setup.sh restart.sh'));
    console.log(chalk.dim('  ./setup.sh'));
    console.log(chalk.dim('  cp .env.example .env'));
    console.log(chalk.dim('  # Edit .env — set CLAUDE_PROJECTS_DIR'));
    console.log(chalk.dim('  ./restart.sh'));
    console.log('');
    printSuccess(fullPath);
    return;
  }

  if (installMethod === 'docker') {
    const spinnerDocker = ora({
      text: chalk.dim('Starting with Docker Compose...'),
      color: 'magenta',
    }).start();

    try {
      await runCommand('docker-compose up -d --build', fullPath);
      spinnerDocker.succeed(chalk.green('Docker containers started'));
    } catch (err) {
      spinnerDocker.fail(chalk.red('Docker failed'));
      console.error(chalk.dim(err.message));
      process.exit(1);
    }

    printSuccess(fullPath);
    return;
  }

  // Auto install
  // Step 2: Configure .env
  const spinnerEnv = ora({
    text: chalk.dim('Configuring environment...'),
    color: 'magenta',
  }).start();

  try {
    const envPath = join(fullPath, '.env');
    const envExamplePath = join(fullPath, '.env.example');

    if (existsSync(envExamplePath) && !existsSync(envPath)) {
      const envContent = `CLAUDE_PROJECTS_DIR=${projectsDir}\nDASHBOARD_HOST=127.0.0.1\nDASHBOARD_PORT=8080\nDEVICE_AUTH_ENABLED=true\n`;
      writeFileSync(envPath, envContent);
    }
    spinnerEnv.succeed(chalk.green('Environment configured'));
  } catch (err) {
    spinnerEnv.warn(chalk.yellow('Could not configure .env — you can do it manually'));
  }

  // Step 3: Run setup.sh
  const spinnerSetup = ora({
    text: chalk.dim('Installing dependencies and building frontend (this may take a minute)...'),
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
    console.log(chalk.dim('\n  Try running manually:'));
    console.log(chalk.dim(`  cd ${projectDir} && ./setup.sh\n`));
    process.exit(1);
  }

  // Step 4: Start server
  if (startServer) {
    console.log('');
    console.log(
      chalk.bold('  🚀 Starting Hivemind...\n')
    );

    const child = spawn('sh', ['-c', './restart.sh'], {
      cwd: fullPath,
      stdio: 'inherit',
      detached: false,
    });

    child.on('error', (err) => {
      console.error(chalk.red(`\n  Failed to start: ${err.message}`));
    });
  } else {
    printSuccess(fullPath);
  }
}

function printSuccess(fullPath) {
  console.log('');
  console.log(
    hivemindGradient(
      '  ╔══════════════════════════════════════════════════════╗'
    )
  );
  console.log(
    hivemindGradient(
      '  ║           🧠 Hivemind is ready!                     ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ╠══════════════════════════════════════════════════════╣'
    )
  );
  console.log(
    hivemindGradient(
      `  ║  📂 Installed at: ${fullPath.padEnd(34)}║`
    )
  );
  console.log(
    hivemindGradient(
      '  ║  🌐 Dashboard:    http://localhost:8080              ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ╠══════════════════════════════════════════════════════╣'
    )
  );
  console.log(
    hivemindGradient(
      '  ║  To start:                                          ║'
    )
  );
  console.log(
    hivemindGradient(
      `  ║  $ cd ${fullPath.split('/').pop().padEnd(46)}║`
    )
  );
  console.log(
    hivemindGradient(
      '  ║  $ ./restart.sh                                     ║'
    )
  );
  console.log(
    hivemindGradient(
      '  ╚══════════════════════════════════════════════════════╝'
    )
  );
  console.log('');
  console.log(chalk.dim('  Now go lie on the couch. Your team has got this. 🛋️'));
  console.log('');
}
