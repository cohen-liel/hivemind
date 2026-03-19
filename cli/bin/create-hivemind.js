#!/usr/bin/env node

import { main } from '../src/index.js';

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
