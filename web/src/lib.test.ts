import { describe, expect, it } from "vitest";
import { STATUS, changeLine, fitRoute, fmtHM, fmtMinutes, fmtPace, goalLine, mercator, parsePace, rangeStats, sampleIndices, trailingAverage, withOption } from "./lib";

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

describe("paceFor / rpeFor", () => {
  it("picks the threshold pace of the requested sport", async () => {
    const { paceFor, PACE_UNIT } = await import("./lib");
    const settings = { run_threshold_pace_s: 270, swim_threshold_pace_s: 105 };
    expect(paceFor("running", settings)).toBe(270);
    expect(paceFor("swimming", settings)).toBe(105);
    expect(paceFor("cycling", settings)).toBeNull();
    expect(paceFor("running", {})).toBeNull();
    expect(PACE_UNIT.swimming).toBe("/100 m");
  });
  it("matches the backend effort scale", async () => {
    const { rpeFor } = await import("./lib");
    expect(rpeFor(0.4)).toBe("rest");
    expect(rpeFor(0.85)).toBe("5–6");
    expect(rpeFor(1.0)).toBe("7");
    expect(rpeFor(1.3)).toBe("9");
  });
});

describe("training plan helpers", () => {
  it("formats minutes and paces like the server", () => {
    expect(fmtMinutes(45)).toBe("45 min");
    expect(fmtMinutes(60)).toBe("1 h");
    expect(fmtMinutes(75)).toBe("1 h 15");
    expect(fmtMinutes(125)).toBe("2 h 05");
    expect(fmtPace(270)).toBe("4:30");
    expect(parsePace("4:30")).toBe(270);
    expect(parsePace("1.45")).toBe(105);
    expect(parsePace("270")).toBe(270);
    expect(parsePace("4:75")).toBeNull();
    expect(parsePace("fast")).toBeNull();
  });

  it("writes a change as one line", () => {
    const day = () => "Tue";
    expect(changeLine({ day: "2026-11-17", slot: 0, before_label: "Threshold ride, 1 h 15", after_label: "Endurance ride, 1 h" }, day))
      .toBe("Tue: Threshold ride, 1 h 15 → Endurance ride, 1 h");
    expect(changeLine({ day: "2026-11-17", slot: 1, before_label: "Gym: core, 30 min", after_label: "Nothing" }, day))
      .toBe("Tue (2nd): Gym: core, 30 min → Rest day");
    expect(changeLine({ day: "2026-11-17", slot: 0, before_label: "Nothing", after_label: "Gym: core, 30 min" }, day))
      .toBe("Tue: free → Gym: core, 30 min");
  });

  it("adds the planned minutes to the time options", () => {
    expect(withOption([30, 60, 90], 75)).toEqual([30, 60, 75, 90]);
    expect(withOption([30, 60, 90], 60)).toEqual([30, 60, 90]);
    expect(withOption([30, 60, 90], null)).toEqual([30, 60, 90]);
  });

  it("describes goals", () => {
    expect(goalLine({ kind: "volume", sport: null, date: null, target: 8, name: "" }, "2026-11-13")).toBe("8 hours a week");
    expect(goalLine({ kind: "general", sport: null, date: null, target: null, name: "Stay fit" }, "2026-11-13")).toBe("Stay fit · General fitness");
    expect(goalLine({ kind: "performance", sport: "running", date: null, target: 270, name: "" }, "2026-11-13")).toBe("4:30 /km threshold pace");
    const event = goalLine({ kind: "event", sport: "cycling", date: "2026-12-11", target: 4, name: "Fondo" }, "2026-11-13");
    expect(event.startsWith("Fondo · 4 h ride on ")).toBe(true);
    expect(event.endsWith("(4 weeks)")).toBe(true);
  });

  it("labels every day status", () => {
    expect(STATUS.missed.tone).toBe("bad");
    expect(STATUS.done.label).toBe("Done");
  });
});
