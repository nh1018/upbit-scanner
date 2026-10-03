// Production source supplied by user; only pasted Markdown escapes normalized.
export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("BTC Anytime Webhook is running", {
        status: 200
      });
    }

    try {
      const data = await request.json();

      // 1. TradingView Webhook Secret 검증
      if (!data.secret || data.secret !== env.TV_WEBHOOK_SECRET) {
        console.log("Rejected: invalid webhook secret");

        return new Response(
          JSON.stringify({
            ok: false,
            error: "Unauthorized"
          }),
          {
            status: 401,
            headers: { "Content-Type": "application/json" }
          }
        );
      }

      // 2. 진단 로그
      console.log("Validation diagnostic:", JSON.stringify({
        symbol: data.symbol,
        timeframe: data.timeframe,
        time_ok: Number.isFinite(data.time),
        open_ok: Number.isFinite(data.open),
        high_ok: Number.isFinite(data.high),
        low_ok: Number.isFinite(data.low),
        close_ok: Number.isFinite(data.close),
        volume_ok: Number.isFinite(data.volume),
        oi_ok: Number.isFinite(data.oi)
      }));

      // 3. 데이터 검증
      if (
        data.symbol !== "BTCUSDT.P" ||
        data.timeframe !== "15m" ||
        !Number.isFinite(data.time) ||
        !Number.isFinite(data.open) ||
        !Number.isFinite(data.high) ||
        !Number.isFinite(data.low) ||
        !Number.isFinite(data.close) ||
        !Number.isFinite(data.volume) ||
        !Number.isFinite(data.oi)
      ) {
        console.log("Rejected: invalid market data");

        return new Response(
          JSON.stringify({
            ok: false,
            error: "Invalid market data"
          }),
          {
            status: 400,
            headers: { "Content-Type": "application/json" }
          }
        );
      }

      // 4. 안전하게 저장할 데이터
      const safeData = {
        received_at_utc: new Date().toISOString(),
        symbol: data.symbol,
        timeframe: data.timeframe,
        time: data.time,
        candle_time_utc: new Date(data.time).toISOString(),
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
        volume: data.volume,
        oi: data.oi
      };

      // 5. GitHub 저장은 비동기 처리
      ctx.waitUntil(saveToGitHub(safeData, env));

      console.log("TradingView webhook accepted");
      console.log(JSON.stringify(safeData));

      // TradingView에는 즉시 성공 응답
      return new Response(
        JSON.stringify({
          ok: true,
          accepted: true
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" }
        }
      );

    } catch (error) {
      console.log("Worker request error: " + error.message);

      return new Response(
        JSON.stringify({
          ok: false,
          error: "Internal error"
        }),
        {
          status: 500,
          headers: { "Content-Type": "application/json" }
        }
      );
    }
  }
};


async function saveToGitHub(safeData, env) {
  try {
    const owner = "nh1018";
    const repo = "upbit-scanner";
    const branch = "main";

    const headers = {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "btc-anytime-cloudflare-worker"
    };

    async function getGitHubFile(path) {
      const apiUrl =
        `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;

      const response = await fetch(apiUrl, {
        method: "GET",
        headers
      });

      if (response.status === 404) {
        return null;
      }

      if (!response.ok) {
        const errorText = await response.text();

        throw new Error(
          `GitHub GET failed: ${response.status} ${errorText}`
        );
      }

      return await response.json();
    }


    function textToBase64(text) {
      const bytes = new TextEncoder().encode(text);
      let binary = "";

      for (const byte of bytes) {
        binary += String.fromCharCode(byte);
      }

      return btoa(binary);
    }


    function base64ToText(base64) {
      const binary = atob(base64.replace(/\n/g, ""));
      const bytes = new Uint8Array(binary.length);

      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }

      return new TextDecoder().decode(bytes);
    }


    async function putGitHubFile(path, text, message, sha = null) {
      const apiUrl =
        `https://api.github.com/repos/${owner}/${repo}/contents/${path}`;

      const body = {
        message,
        content: textToBase64(text),
        branch
      };

      if (sha) {
        body.sha = sha;
      }

      const response = await fetch(apiUrl, {
        method: "PUT",
        headers: {
          ...headers,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        const errorText = await response.text();

        throw new Error(
          `GitHub PUT failed: ${response.status} ${errorText}`
        );
      }

      return await response.json();
    }


    // ===== latest =====

    const latestPath =
      "output/btc_anytime_webhook_latest.json";

    const latestExisting =
      await getGitHubFile(latestPath);

    const latestText =
      JSON.stringify(safeData, null, 2) + "\n";

    await putGitHubFile(
      latestPath,
      latestText,
      "Update BTC Anytime latest data",
      latestExisting ? latestExisting.sha : null
    );


    // ===== 15m History =====

    const candleDate =
      new Date(safeData.time)
        .toISOString()
        .slice(0, 10)
        .replace(/-/g, "");

    const historyPath =
      `data_market/btc_anytime/15m/btc_15m_${candleDate}.jsonl`;

    const historyExisting =
      await getGitHubFile(historyPath);

    let historyText = "";
    let historySha = null;

    if (historyExisting) {
      historySha = historyExisting.sha;
      historyText =
        base64ToText(historyExisting.content);
    }


    // 중복 검사
    let duplicate = false;

    if (historyText.trim()) {
      const lines =
        historyText.trim().split("\n");

      for (const line of lines) {
        try {
          const row = JSON.parse(line);

          if (row.time === safeData.time) {
            duplicate = true;
            break;
          }
        } catch (_) {
          // 기존 손상 행 때문에 전체 저장 중단하지 않음
        }
      }
    }


    // 신규 확정 15분봉만 추가
    if (!duplicate) {
      if (
        historyText.length > 0 &&
        !historyText.endsWith("\n")
      ) {
        historyText += "\n";
      }

      historyText +=
        JSON.stringify(safeData) + "\n";

      await putGitHubFile(
        historyPath,
        historyText,
        `Append BTC 15m candle ${safeData.candle_time_utc}`,
        historySha
      );

      console.log("BTC 15m history appended");

    } else {
      console.log("BTC 15m duplicate skipped");
    }

    console.log("GitHub latest file updated");

  } catch (error) {
    // TradingView 응답과 분리:
    // GitHub 저장 실패는 로그에 명확하게 남김
    console.log(
      "GitHub background save failed: " +
      error.message
    );

    throw error;
  }
}