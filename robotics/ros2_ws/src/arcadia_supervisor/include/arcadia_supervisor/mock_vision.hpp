#pragma once
#include <functional>

#include "arcadia_supervisor/world_state.hpp"

namespace arcadia {

// Proximity-based mock computer vision. The supervisor only decides *when* the scout UAV has a bed
// section in view (within its sensor footprint); the backend's agronomic world model decides *what*
// the multispectral camera measures there (NDVI, weeds, disease, water stress, with sensor noise).
class MockVision {
 public:
  using EventSink = std::function<void(const std::string &kind, const std::string &robot, const std::string &section,
                                       const std::string &severity, const json &payload)>;
  MockVision(World &w, EventSink sink) : w_(w), emit_(std::move(sink)) {}
  // Emit `section_seen` for sections inside the footprint, at most once per `cooldown` seconds each.
  void scan(const RobotRef &uav, double radius, double now, double cooldown = 20.0);

 private:
  World &w_;
  EventSink emit_;
};

}  // namespace arcadia
