const OWNER = "hcnk3HrzQe";
const REPO = "jiuxiao-macau-trend";
const WF = "collect_hk.yml";

async function trigger(env) {
  const r = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WF}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "cf-worker-timer",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  return { status: r.status, body: (await r.text()).slice(0, 300) };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      (async () => {
        try {
          const res = await trigger(env);
          console.log("cron dispatch ->", res.status, res.body);
        } catch (e) {
          console.log("cron dispatch ERR ->", e && e.message);
        }
      })()
    );
  },
  async fetch(request, env) {
    try {
      const res = await trigger(env);
      return new Response(`dispatch ${res.status}: ${res.body}\n`, { status: 200 });
    } catch (e) {
      return new Response(`ERR ${e && e.message} | ${e && e.stack}\n`, { status: 200 });
    }
  },
};
