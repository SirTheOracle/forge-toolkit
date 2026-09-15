#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { chromium } from "@playwright/test";
try { const browser = await chromium.launch({headless:true}); await browser.close(); }
catch (error) { console.error("Browser launch failed. Run npm run e2e:install.", error); process.exit(1); }
const result=spawnSync("npx",["playwright","test",...process.argv.slice(2)],{stdio:"inherit",env:process.env});
process.exit(result.status ?? 1);