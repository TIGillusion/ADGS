// ============================================================
// 展示格式化工具单元测试
// ============================================================

import { describe, expect, it } from "vitest";
import {
  clampText,
  fmtAgo,
  fmtArgs,
  fmtDuration,
  fmtTime,
  fmtTimeSec,
  pct,
  score,
  scoreTone,
} from "@/lib/format";

describe("pct / score", () => {
  it("pct 正常与空值", () => {
    expect(pct(0.5)).toBe("50%");
    expect(pct(0.123, 1)).toBe("12.3%");
    expect(pct(null)).toBe("—");
    expect(pct(Number.NaN)).toBe("—");
  });

  it("score 一位小数", () => {
    expect(score(88.56)).toBe("88.6");
    expect(score(88)).toBe("88.0");
    expect(score(undefined)).toBe("—");
  });

  it("scoreTone 语义色分档", () => {
    expect(scoreTone(90)).toBe("text-ok-400");
    expect(scoreTone(80)).toBe("text-ok-400");
    expect(scoreTone(79.9)).toBe("text-life-400");
    expect(scoreTone(60)).toBe("text-life-400");
    expect(scoreTone(59.9)).toBe("text-warn-400");
    expect(scoreTone(40)).toBe("text-warn-400");
    expect(scoreTone(39.9)).toBe("text-danger-400");
    expect(scoreTone(null)).toBe("text-ink-500");
  });
});

describe("fmtTime / fmtTimeSec / fmtAgo", () => {
  it("fmtTime unix 秒", () => {
    expect(fmtTime(1700000000)).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/);
  });

  it("fmtTime 字符串/空/非法", () => {
    expect(fmtTime("2024-01-02T03:04:00")).toBe("01-02 03:04");
    expect(fmtTime(null)).toBe("—");
    expect(fmtTime("")).toBe("—");
    expect(fmtTime("not-a-date")).toBe("not-a-date");
  });

  it("fmtTimeSec 含秒", () => {
    expect(fmtTimeSec("2024-01-02T03:04:05")).toBe("01-02 03:04:05");
  });

  it("fmtAgo 秒/分/时/天", () => {
    const now = Date.now() / 1000;
    expect(fmtAgo(now - 10)).toBe("10 秒前");
    expect(fmtAgo(now - 300)).toBe("5 分钟前");
    expect(fmtAgo(now - 7200)).toBe("2 小时前");
    expect(fmtAgo(now - 172800)).toBe("2 天前");
    expect(fmtAgo(null)).toBe("");
    // 未来时间钳制为 0
    expect(fmtAgo(now + 9999)).toBe("0 秒前");
  });
});

describe("fmtDuration", () => {
  it("小时制", () => {
    expect(fmtDuration(3671)).toBe("1:01:11");
  });
  it("分钟制", () => {
    expect(fmtDuration(65)).toBe("01:05");
  });
  it("非法值", () => {
    expect(fmtDuration(-1)).toBe("—");
    expect(fmtDuration(Infinity)).toBe("—");
  });
});

describe("fmtArgs / clampText", () => {
  it("fmtArgs 对象 JSON 化（带缩进便于阅读）", () => {
    expect(fmtArgs({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(fmtArgs("raw string")).toBe("raw string");
    expect(fmtArgs(null)).toBe("");
  });

  it("clampText 截断", () => {
    expect(clampText("abcdef", 4)).toBe("abcd…");
    expect(clampText("abc", 4)).toBe("abc");
    expect(clampText(null, 4)).toBe("");
  });
});
