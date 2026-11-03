#!/usr/bin/env node
// Bridge CLI: spawn `uvx blender-mcp` and call a tool (JSON-RPC over stdio)
// Compatible with blender-mcp v1.29+ which requires user_prompt parameter
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const tool = process.argv[2];
let args = {};
const raw = process.argv[3] || '{}';
if (raw.startsWith('@')) {
  args = JSON.parse(fs.readFileSync(raw.slice(1), 'utf8'));
} else {
  args = JSON.parse(raw);
}

let uvxCmd = 'uvx';
const localBin = path.join(process.env.USERPROFILE, '.local', 'bin', 'uvx.exe');
if (fs.existsSync(localBin)) uvxCmd = localBin;

const proc = spawn(uvxCmd, ['blender-mcp'], {
  stdio: ['pipe', 'pipe', 'pipe'],
  shell: process.platform === 'win32'
});

let nextId = 1;
const pending = new Map();
let initDone = false;

function send(req) {
  return new Promise((resolve, reject) => {
    pending.set(req.id, { resolve, reject });
    proc.stdin.write(JSON.stringify(req) + '\n');
    setTimeout(() => {
      if (pending.has(req.id)) {
        pending.delete(req.id);
        reject(new Error('Timeout calling Blender MCP'));
      }
    }, 300000);
  });
}

// JSON-RPC responses may come on either stdout or stderr with blender-mcp v1.29+
function handleResponse(data) {
  const lines = data.toString().split('\n');
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const resp = JSON.parse(line);
      const p = pending.get(resp.id);
      if (p) {
        pending.delete(resp.id);
        if (resp.error) p.reject(new Error(JSON.stringify(resp.error)));
        else p.resolve(resp.result);
      }
    } catch (e) { /* ignore non-JSON log lines */ }
  }
}

proc.stdout.on('data', handleResponse);
proc.stderr.on('data', handleResponse);

async function main() {
  // Initialize handshake
  await send({
    jsonrpc: '2.0', id: nextId++, method: 'initialize',
    params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'wb-blender-bridge', version: '1.0.0' } }
  });
  await send({ jsonrpc: '2.0', id: nextId++, method: 'notifications/initialized', params: {} }).catch(() => {});
  initDone = true;

  if (tool === 'list_tools') {
    const r = await send({ jsonrpc: '2.0', id: nextId++, method: 'tools/list', params: {} });
    console.log(JSON.stringify(r, null, 2));
  } else {
    // New API requires user_prompt for all tool calls
    const callArgs = { user_prompt: args.user_prompt || '用户操作' };
    // Merge other args
    for (const [k, v] of Object.entries(args)) {
      if (k !== 'user_prompt') callArgs[k] = v;
    }
    const r = await send({ jsonrpc: '2.0', id: nextId++, method: 'tools/call', params: { name: tool, arguments: callArgs } });
    console.log(typeof r === 'string' ? r : JSON.stringify(r, null, 2));
  }
  proc.kill();
  process.exit(0);
}

main().catch((e) => { console.error('ERROR:', e.message); proc.kill(); process.exit(1); });
