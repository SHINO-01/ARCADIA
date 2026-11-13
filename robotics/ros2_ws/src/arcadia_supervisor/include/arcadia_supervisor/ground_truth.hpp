#pragma once
#include <functional>
#include <random>

#include "arcadia_supervisor/world_spawner.hpp"
#include "arcadia_supervisor/world_state.hpp"

namespace arcadia {

// Owns agronomy ground truth and the proximity-based mock computer vision.
class GroundTruth {
 public:
  using EventSink = std::function<void(const std::string &kind, const std::string &robot, const std::string &h3,
                                       const std::string &severity, const json &payload)>;
  GroundTruth(World &w, WorldSpawner &spawner, EventSink sink) : w_(w), spawner_(spawner), emit_(std::move(sink)) {}
  void seed(unsigned seed);
  void initialise();                 // sprinkle hidden weeds / dryness after a world load
  void grow(double dt);              // accelerated crop lifecycle
  void observe(const RobotRef &uav, double radius);          // mock multispectral CV
  void onRobotEvent(const std::string &kind, const std::string &h3, const json &payload);  // seeded/weeded/harvested

 private:
  World &w_;
  WorldSpawner &spawner_;
  EventSink emit_;
  std::mt19937 rng_{7};
};

}  // namespace arcadia
