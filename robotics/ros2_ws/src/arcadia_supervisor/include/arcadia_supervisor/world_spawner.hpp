#pragma once
#include "arcadia_supervisor/world_state.hpp"

namespace arcadia {

// Procedurally builds / tears down the farm scene from the world-config JSON using primitives.
class WorldSpawner {
 public:
  explicit WorldSpawner(webots::Supervisor &sup) : sup_(sup) {}
  void load(const json &cfg, World &world);
  void clear(World &world);
  void setCropState(Cell &c);      // resize/recolour the crop cylinder to reflect its state
  void setWeeds(Cell &c, bool on);

 private:
  webots::Supervisor &sup_;
  webots::Field *farmChildren();
  void spawnTerrain(const json &cfg);
  void spawnCells(const json &cfg, World &world);
  void spawnInfra(const json &cfg, World &world);
  void spawnRobots(const json &cfg, World &world);
  int imported_ = 0;
};

}  // namespace arcadia
