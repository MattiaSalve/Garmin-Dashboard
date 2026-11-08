import { describe, expect, it } from "vitest";
import { fmtHM, rangeStats, sampleIndices, trailingAverage } from "./lib";

describe("rangeStats", () => {
  const minutes = [0, 10, 20, 30];
  it("averages channels and totals elevation gain", () => {
    const stats = rangeStats(
      {
        Elevation: { unit: "m", values: [100, 110, 105, 120] },
        Power: { unit: "W", values: [200, 220, 0, 240] },
      },
      minutes,
      [0, 1, 2, 3],
      0,
      3,
    );
    expect(stats["Elevation gain"]).toBe("25 m");
    expect(stats["Avg power"]).toBe("165 W");
    expect(stats["Distance"]).toBe("3.0 km");
    expect(stats["Duration"]).toBe("30 min");
  });
});

describe("trailingAverage", () => {
  it("is a running mean without a window", () => {
    expect(trailingAverage([2, 4, 6], [0, 1 / 60, 2 / 60], null)).toEqual([2, 3, 4]);
  });
  it("only looks back over the window", () => {
    // samples one second apart, 2 s window: each point averages itself and the one before
    expect(trailingAverage([2, 4, 6], [0, 1 / 60, 2 / 60], 2)).toEqual([2, 3, 5]);
  });
});

it("formats hours and minutes", () => {
  expect(fmtHM(4500)).toBe("1:15");
  expect(fmtHM(null)).toBe("–");
});

it("samples evenly and always keeps the last point", () => {
  const idx = sampleIndices(0, 9999, 1000);
  expect(idx.length).toBeLessThanOrEqual(1001);
  expect(idx[idx.length - 1]).toBe(9999);
});
