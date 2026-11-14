#pragma once
// Shared state for the ARCADIA Webots supervisor: the parsed world config, the spawned nodes,
// and crop ground truth (which the backend only learns about via observations).
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>
#include <webots/Node.hpp>
#include <webots/Supervisor.hpp>

namespace arcadia {

using json = nlohmann::json;

enum class CropState { Fallow, Seeded, Growing, Ripe, Harvested, Infra };

struct Cell {
  std::string h3;
  double x = 0, y = 0;
  std::string kind;      // crop | dock | storage
  std::string crop;      // wheat | maize | ...
  double crop_height = 0.6;
  CropState state = CropState::Fallow;
  bool weeds = false;
  double moisture = 0.5;
  double health = 1.0;
  double t_state = 0.0;
  bool waterlogged = false;
  std::string observed_sig;
  webots::Node *node = nullptr;       // crop cylinder Solid
  webots::Node *weed_node = nullptr;  // weed marker (small red sphere), hidden when clean
};

struct RobotRef {
  std::string id, type, home;
  bool active = true;
  webots::Node *node = nullptr;
  std::string towing;      // robot id this robot is towing (AGV only)
  bool tow_attached = false;
  bool pinned = false;     // terrain entrapment: chassis held in place while wheels spin
  double pin_x = 0, pin_y = 0, pin_yaw = 0;
  double stuck_cooldown = 0; // seconds before this robot may get stuck again
};

struct Infra {
  std::string id, kind;
  double x = 0, y = 0;
  webots::Node *node = nullptr;
};

struct World {
  json config;
  std::string farm_id;
  std::map<std::string, Cell> cells;
  std::map<std::string, RobotRef> robots;
  std::map<std::string, Infra> infra;
  double edge_m = 10.0;
  bool loaded = false;
};

std::string stateName(CropState s);
CropState stateFrom(const std::string &s);

}  // namespace arcadia
