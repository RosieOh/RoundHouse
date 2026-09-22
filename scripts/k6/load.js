// Open-model load: requests arrive at RATE/s whether or not earlier ones have
// finished, which is how real clients behave and what makes queueing and
// autoscaling visible (a closed VU loop would just slow down instead).
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL;
const KEY = __ENV.LITELLM_KEY;
const RATE = parseInt(__ENV.RATE || "12", 10);
const DURATION = __ENV.DURATION || "3m";
const STREAM_SHARE = parseFloat(__ENV.STREAM_SHARE || "0.2");

export const options = {
  scenarios: {
    chat: {
      executor: "ramping-arrival-rate",
      startRate: 1,
      timeUnit: "1s",
      preAllocatedVUs: 40,
      maxVUs: 200,
      stages: [
        { target: RATE, duration: "30s" },
        { target: RATE, duration: DURATION },
        { target: 0, duration: "15s" },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.02"],
    "http_req_duration{stream:false}": ["p(95)<3000"],
  },
};

const MODELS = ["gpt-mock", "gpt-mock", "gpt-mock", "claude-mock"];

export default function () {
  const model = MODELS[Math.floor(Math.random() * MODELS.length)];
  const stream = Math.random() < STREAM_SHARE;
  const body = JSON.stringify({
    model,
    stream,
    messages: [{ role: "user", content: `load test ${__VU}-${__ITER}` }],
  });
  const res = http.post(`${BASE_URL}/v1/chat/completions`, body, {
    headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
    timeout: "60s",
    tags: { model, stream: String(stream) },
  });
  check(res, { "status is 200": (r) => r.status === 200 });
}
