import { describe, expect, it, vi } from "vitest";
import {
  PROCESSING_CUE_INITIAL_DELAY_MS,
  PROCESSING_CUE_REPEAT_MS,
  ProcessingCueController,
  type CueScheduler,
} from "./processingCue";

class FakeScheduler implements CueScheduler {
  private now = 0;
  private nextId = 1;
  private tasks = new Map<number, { at: number; callback: () => void }>();

  setTimeout(callback: () => void, delayMs: number): number {
    const id = this.nextId++;
    this.tasks.set(id, { at: this.now + delayMs, callback });
    return id;
  }

  clearTimeout(id: number): void {
    this.tasks.delete(id);
  }

  advance(delayMs: number): void {
    const target = this.now + delayMs;
    while (true) {
      const due = [...this.tasks.entries()]
        .filter(([, task]) => task.at <= target)
        .sort((left, right) => left[1].at - right[1].at)[0];
      if (!due) break;
      this.tasks.delete(due[0]);
      this.now = due[1].at;
      due[1].callback();
    }
    this.now = target;
  }
}

describe("ProcessingCueController", () => {
  it("waits three seconds, then repeats every seven seconds", () => {
    const scheduler = new FakeScheduler();
    const play = vi.fn();
    const controller = new ProcessingCueController(play, undefined, scheduler);

    controller.begin();
    scheduler.advance(PROCESSING_CUE_INITIAL_DELAY_MS - 1);
    expect(play).not.toHaveBeenCalled();

    scheduler.advance(1);
    expect(play).toHaveBeenCalledTimes(1);

    scheduler.advance(PROCESSING_CUE_REPEAT_MS - 1);
    expect(play).toHaveBeenCalledTimes(1);
    scheduler.advance(1);
    expect(play).toHaveBeenCalledTimes(2);
  });

  it("does not reset the grace period when processing is reported twice", () => {
    const scheduler = new FakeScheduler();
    const play = vi.fn();
    const controller = new ProcessingCueController(play, undefined, scheduler);

    controller.begin();
    scheduler.advance(2_000);
    controller.begin();
    scheduler.advance(1_000);

    expect(play).toHaveBeenCalledTimes(1);
  });

  it("cancels pending and repeating cues immediately", () => {
    const scheduler = new FakeScheduler();
    const play = vi.fn();
    const silence = vi.fn();
    const controller = new ProcessingCueController(play, silence, scheduler);

    controller.begin();
    scheduler.advance(PROCESSING_CUE_INITIAL_DELAY_MS);
    expect(play).toHaveBeenCalledTimes(1);

    controller.stop();
    expect(silence).toHaveBeenCalledTimes(1);
    scheduler.advance(PROCESSING_CUE_REPEAT_MS * 2);
    expect(play).toHaveBeenCalledTimes(1);

    controller.begin();
    controller.stop();
    scheduler.advance(PROCESSING_CUE_INITIAL_DELAY_MS);
    expect(play).toHaveBeenCalledTimes(1);
  });
});
