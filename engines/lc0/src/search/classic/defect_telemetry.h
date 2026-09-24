/*
  This file is part of Leela Chess Zero.
  Copyright (C) 2026 The AllfatherChess Authors

  Leela Chess is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
*/

#pragma once

#include <cstdint>
#include <unordered_map>

namespace lczero {
namespace classic {

class DefectSpeculativeProvenance {
 public:
  struct Token {
    uint64_t position_hash = 0;
    uint64_t generation = 0;
  };

  Token Register(uint64_t position_hash) {
    auto& state = states_[position_hash];
    ++inflight_;
    return Token{position_hash, state.generation};
  }

  bool Complete(const Token& token) {
    if (inflight_ > 0) --inflight_;
    auto it = states_.find(token.position_hash);
    if (it == states_.end() || it->second.generation != token.generation) {
      return false;
    }
    ++it->second.eligible;
    return true;
  }

  bool ConsumeOne(uint64_t position_hash) {
    auto it = states_.find(position_hash);
    if (it == states_.end() || it->second.eligible == 0) return false;
    --it->second.eligible;
    return true;
  }

  uint64_t RetireEligibleAndAdvanceGeneration(uint64_t position_hash) {
    auto& state = states_[position_hash];
    const uint64_t retired = state.eligible;
    state.eligible = 0;
    ++state.generation;
    return retired;
  }

  uint64_t OutstandingCount() const {
    uint64_t total = inflight_;
    for (const auto& [hash, state] : states_) {
      (void)hash;
      total += state.eligible;
    }
    return total;
  }

 private:
  struct State {
    uint64_t generation = 0;
    uint64_t eligible = 0;
  };

  std::unordered_map<uint64_t, State> states_;
  uint64_t inflight_ = 0;
};

}  // namespace classic
}  // namespace lczero
