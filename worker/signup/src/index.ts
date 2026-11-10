// SPDX-License-Identifier: Apache-2.0
import { handleFetch, runSender } from "./app";
import { withMailer } from "./brevo";
import type { AppEnv } from "./env";

export default {
  async fetch(request: Request, env: AppEnv): Promise<Response> {
    return handleFetch(request, withMailer(env));
  },
  async scheduled(_event: ScheduledController, env: AppEnv, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(
      runSender(withMailer(env)).then((report) => console.log("sender", JSON.stringify(report))),
    );
  },
} satisfies ExportedHandler<AppEnv>;
