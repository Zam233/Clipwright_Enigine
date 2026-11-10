#!/usr/bin/env node
/**
 * motion_scene 插件的 Remotion 运行时（插件自有的 Node 资产）。
 *
 * engine 只通过 job broker 执行本脚本；场景解析、TSX 编译、Chrome 渲染、
 * 透明编码全部在这里完成，engine 不感知 Remotion。
 *
 * 用法：
 *   node render.mjs --mode video --scene motion/scenes/scene-001.tsx \
 *        --out renders/scene.mov --width 1920 --height 1080 --fps 30 --duration 4
 *   node render.mjs --mode still --scene ... --out renders/preview.png --frame 0 ...
 *
 * 约定：scene 文件默认导出 React 组件，可用 `remotion` 的 useCurrentFrame/useVideoConfig。
 */
import {spawnSync} from 'node:child_process';
import {copyFileSync, existsSync, mkdirSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const RUNTIME_DIR = dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith('--')) continue;
    const name = key.slice(2);
    const value = argv[i + 1];
    if (value === undefined || value.startsWith('--')) {
      out[name] = true;
    } else {
      out[name] = value;
      i += 1;
    }
  }
  return out;
}

function findChrome() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const candidates = process.platform === 'win32'
    ? [
        'C:/Program Files/Google/Chrome/Application/chrome.exe',
        'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
        'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      ]
    : process.platform === 'darwin'
      ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
      : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  return candidates.find((p) => existsSync(p)) || '';
}

const args = parseArgs(process.argv.slice(2));
const mode = args.mode === 'still' ? 'still' : 'video';
const scene = resolve(process.cwd(), String(args.scene || ''));
const out = resolve(process.cwd(), String(args.out || (mode === 'still' ? 'renders/preview.png' : 'renders/scene.mov')));
const width = Number(args.width || 1920);
const height = Number(args.height || 1080);
const fps = Number(args.fps || 30);
const duration = Number(args.duration || 2);
const frame = Number(args.frame || 0);
const frames = Math.max(1, Math.round(duration * fps));

if (!existsSync(scene)) {
  console.error(`scene 不存在: ${scene}`);
  process.exit(2);
}

const scenesDir = join(RUNTIME_DIR, 'scenes');
mkdirSync(scenesDir, {recursive: true});
const sceneCopy = join(scenesDir, 'scene.tsx');
copyFileSync(scene, sceneCopy);

const entry = join(RUNTIME_DIR, 'entry.gen.tsx');
writeFileSync(entry, [
  "import React from 'react';",
  "import {Composition, registerRoot} from 'remotion';",
  "import Scene from './scenes/scene';",
  `const Root = () => React.createElement(Composition, {`,
  `  id: 'Scene', component: Scene, durationInFrames: ${frames},`,
  `  fps: ${fps}, width: ${width}, height: ${height},`,
  `});`,
  'registerRoot(Root);',
  '',
].join('\n'), 'utf8');

mkdirSync(dirname(out), {recursive: true});
const chrome = findChrome();
// 直接跑 @remotion/cli 的 JS 入口：避免 Windows 下 npx.cmd + shell 引号把
// 含空格的路径拆坏（Chrome 在 "C:/Program Files/..."）。
const cliEntry = join(RUNTIME_DIR, 'node_modules', '@remotion', 'cli', 'remotion-cli.js');

const cliArgs = mode === 'still'
  ? [cliEntry, 'still', entry, 'Scene', out,
     '--frame', String(frame), '--overwrite', '--log', 'error']
  : [cliEntry, 'render', entry, 'Scene', out,
     '--codec', 'prores', '--prores-profile', '4444',
     '--pixel-format', 'yuva444p10le', '--image-format', 'png',
     '--overwrite', '--log', 'error', '--concurrency', '1'];
if (chrome) cliArgs.push('--browser-executable', chrome);

const result = spawnSync(process.execPath, cliArgs, {
  cwd: RUNTIME_DIR,
  stdio: 'inherit',
  env: {...process.env, NODE_OPTIONS: process.env.NODE_OPTIONS || ''},
});
if (result.status !== 0) {
  console.error(`remotion ${mode} 失败，退出码 ${result.status}`);
  process.exit(result.status || 1);
}
console.log(JSON.stringify({ok: true, mode, out, width, height, fps, duration, frames}));
