import { describe, expect, it } from "vitest";
import { fitRoute, fmtHM, mercator, rangeStats, sampleIndices, trailingAverage } from "./lib";

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

describe("fitRoute", () => {
  const route: [number, number][] = [[46.0, 8.0], [46.01, 8.02], [46.02, 8.0]];
  it("keeps every point inside the box with padding", () => {
    const { points } = fitRoute(route, 800, 400, 28);
    for (const [x, y] of points) {
      expect(x).toBeGreaterThanOrEqual(28 - 1e-6);
      expect(x).toBeLessThanOrEqual(772 + 1e-6);
      expect(y).toBeGreaterThanOrEqual(28 - 1e-6);
      expect(y).toBeLessThanOrEqual(372 + 1e-6);
    }
  });
  it("puts north at the top", () => {
    const { points } = fitRoute(route, 800, 400);
    expect(points[2][1]).toBeLessThan(points[0][1]);
  });
  it("maps the equator and prime meridian to the world centre", () => {
    expect(mercator(0, 0)).toEqual([0.5, 0.5]);
  });
});
