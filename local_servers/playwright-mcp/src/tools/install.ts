/**
 * Copyright (c) Microsoft Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { fork } from 'child_process';
import { existsSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import * as playwright from 'playwright';
import { z } from 'zod';
import { defineTool } from './tool.js';


const install = defineTool({
  capability: 'core-install',
  schema: {
    name: 'browser_install',
    title: 'Install the browser specified in the config',
    description: 'Install the browser specified in the config. Call this if you get an error about the browser not being installed.',
    inputSchema: z.object({}),
    type: 'destructive',
  },

  handle: async (context, params, response) => {
    const browserName = context.config.browser?.browserName ?? 'chromium';
    const browserType = playwright[browserName];
    const executablePath = browserType.executablePath();
    if (existsSync(executablePath)) {
      response.addResult(`Browser '${browserName}' is already installed at ${executablePath}.`);
      response.setIncludeTabs();
      return;
    }

    if (process.env.TOOLATHLON_ALLOW_RUNTIME_BROWSER_INSTALL !== '1') {
      response.addResult(
        `Browser '${browserName}' was not found at ${executablePath}. ` +
        `Runtime browser downloads are disabled in Toolathlon images; rebuild ` +
        `the image so the Dockerfile installs the Node Playwright browser cache.`
      );
      response.setIncludeTabs();
      return;
    }

    const channel = context.config.browser?.launchOptions?.channel ?? browserName ?? 'chrome';
    const cliUrl = import.meta.resolve('playwright/package.json');
    const cliPath = path.join(fileURLToPath(cliUrl), '..', 'cli.js');
    const child = fork(cliPath, ['install', channel], {
      stdio: 'pipe',
    });
    const output: string[] = [];
    child.stdout?.on('data', data => output.push(data.toString()));
    child.stderr?.on('data', data => output.push(data.toString()));
    await new Promise<void>((resolve, reject) => {
      child.on('close', code => {
        if (code === 0)
          resolve();
        else
          reject(new Error(`Failed to install browser: ${output.join('')}`));
      });
    });
    response.setIncludeTabs();
  },
});

export default [
  install,
];
