#pragma once
#include "arcadia_supervisor/world_state.hpp"

namespace arcadia {

// Procedurally builds / tears down the farm scene from the world-config JSON using primitives.
class WorldSpawner {
 public:
  explicit WorldSpawner(webots::Supervisor &sup) : sup_(sup) {}
  void load(const json &cfg, World &world);
  void clear(World &world);
  void setCropState(Section &c);   // resize/recolour the bed box to reflect its state
  void setWeeds(Section &c, bool on);
  // Paint the world model's state: crop height (m), colour (0-1 RGB) and the weed marker.
  void setVisual(Section &c, double height, double r, double g, double b, bool weeds);

 private:
  webots::Supervisor &sup_;
  webots::Field *farmChildren();
  void spawnTerrain(const json &cfg);
  void spawnFieldLayout(const json &cfg, World &world);  // beds, tracks, hazards (batched)
  void spawnInfra(const json &cfg, World &world);
  void spawnRobots(const json &cfg, World &world);
  int imported_ = 0;
};

}  // namespace arcadia
