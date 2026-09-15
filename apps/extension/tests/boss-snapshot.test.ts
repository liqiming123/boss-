import { describe, expect, it } from "vitest";
import { shiftConfidence } from "../src/adapters/boss/boss-snapshot";

/**
 * The historical chat capture stitches several viewport tiles into one long
 * image. BOSS sometimes swallows the scroll, so a tile can repeat the previous
 * viewport; the stitcher must recognise that instead of duplicating a band.
 */
describe("chat snapshot tile verification", () => {
  it("accepts a tile that advanced by exactly the applied shift", () => {
    const previous = [10, 20, 30, 40, 50, 60, 70, 80];
    // The previous viewport reappears two rows lower in the new one.
    const current = [99, 99, 10, 20, 30, 40, 50, 60, 70, 80];
    expect(shiftConfidence(previous, current, 2, 1)).toBe(1);
  });

  it("rejects a tile that repeats the previous viewport", () => {
    const previous = [10, 20, 30, 40, 50, 60, 70, 80];
    const current = [10, 20, 30, 40, 50, 60, 70, 80];
    // The scroll reported movement, but the pixels did not: this is the
    // duplicated-band bug.
    expect(shiftConfidence(previous, current, 3, 1)).toBeLessThan(0.6);
  });

  it("tolerates small rendering differences between tiles", () => {
    const previous = [10, 20, 30, 40, 50, 60, 70, 80];
    const current = [99, 99, 12, 18, 33, 38, 52, 58, 72, 78];
    expect(shiftConfidence(previous, current, 2, 1)).toBeGreaterThan(0.6);
  });

  it("returns undefined when pixels cannot be compared", () => {
    // No signature available (tainted or detached canvas) must not veto a tile.
    expect(shiftConfidence(undefined, [1, 2, 3], 1, 1)).toBeUndefined();
    expect(shiftConfidence([1, 2, 3], undefined, 1, 1)).toBeUndefined();
    // A shift that cannot overlap anything is also inconclusive.
    expect(shiftConfidence([1, 2, 3], [4, 5, 6], 9, 1)).toBeUndefined();
  });
});
