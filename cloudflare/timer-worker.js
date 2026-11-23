// 사팔사팔 알람시계 (Cloudflare Worker)
// 정해진 시간마다 GitHub에 "update 작업 실행해!" 신호만 보내요.
// GitHub 예약 실행이 늦거나 빠지는 문제(2026-11-23 확인)를 막기 위한 것.
// 필요한 비밀값: GH_TOKEN (GitHub 토큰, Actions 실행 권한만 있는 것)
//
// Cron Triggers (UTC 기준, 한국시간 = UTC+9):
//   45 22 * * SUN-THU      → 평일 07:45 아침
//   32,45 0 * * MON-FRI    → 09:32, 09:45 오전 예측
//   35,50 3 * * MON-FRI    → 12:35, 12:50 오후 예측
//   15 7 * * MON-FRI       → 16:15 실제 결과

const REPO = "hy071015/sapalsapal";
const WORKFLOW = "update.yml";

async function dispatch(env) {
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "sapalsapal-timer",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204) {
    throw new Error(`GitHub 실행 요청 실패: ${res.status} ${await res.text()}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // 주소로 들어오면 아무것도 실행하지 않고 안내만 (누가 접속해도 실행되지 않게)
  async fetch() {
    return new Response("sapalsapal timer: OK", { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
