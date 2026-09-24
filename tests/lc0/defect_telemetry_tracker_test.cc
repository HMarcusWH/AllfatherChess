#include <cassert>
#include <iostream>

#include "search/classic/defect_telemetry.h"

using lczero::classic::DefectSpeculativeProvenance;

int main() {
  DefectSpeculativeProvenance tracker;

  const auto consumed = tracker.Register(0x10);
  assert(tracker.OutstandingCount() == 1);
  assert(tracker.Complete(consumed));
  assert(tracker.ConsumeOne(0x10));
  assert(tracker.OutstandingCount() == 0);

  const auto evicted = tracker.Register(0x20);
  assert(tracker.Complete(evicted));
  assert(tracker.RetireEligibleAndAdvanceGeneration(0x20) == 1);
  assert(!tracker.ConsumeOne(0x20));

  const auto inflight_old = tracker.Register(0x30);
  assert(tracker.RetireEligibleAndAdvanceGeneration(0x30) == 0);
  assert(!tracker.Complete(inflight_old));

  const auto inflight_new = tracker.Register(0x30);
  assert(tracker.Complete(inflight_new));
  assert(tracker.ConsumeOne(0x30));

  const auto duplicate_a = tracker.Register(0x40);
  const auto duplicate_b = tracker.Register(0x40);
  assert(tracker.Complete(duplicate_a));
  assert(tracker.Complete(duplicate_b));
  assert(tracker.OutstandingCount() == 2);
  assert(tracker.ConsumeOne(0x40));
  assert(tracker.OutstandingCount() == 1);
  assert(tracker.ConsumeOne(0x40));

  const auto x = tracker.Register(0x50);
  const auto y = tracker.Register(0x60);
  assert(tracker.Complete(x));
  assert(tracker.Complete(y));
  assert(tracker.RetireEligibleAndAdvanceGeneration(0x50) == 1);
  assert(!tracker.ConsumeOne(0x50));
  assert(tracker.ConsumeOne(0x60));
  assert(tracker.OutstandingCount() == 0);

  std::cout << "lc0 defect telemetry provenance tracker passed\n";
  return 0;
}
