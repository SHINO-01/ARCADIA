#pragma once
// Shared state for the ARCADIA Webots supervisor: the parsed world config and the spawned nodes.
// Crop ground truth lives in the backend's agronomic world model; the supervisor renders it.
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

// A bed section: the agronomic unit (state, observation, work). Rendered as one box.
struct Section {
  std::string id, bed, crop;
  double x = 0, y = 0;          // centre (ENU)
  double length = 10, width = 4, angle = 0;
  double crop_height = 0.6;
  CropState state = CropState::Fallow;
  bool weeds = false;
  double moisture = 0.5;
  double vis_h = 0.05;                // rendered crop height (m), from the world model
  double seen_t = -1e9;               // last time the scout UAV had it in view (mock CV cooldown)
  webots::Node *node = nullptr;       // bed / crop box
  webots::Node *weed_node = nullptr;  // weed marker above the bed, hidden when clean
};

// Waterlogged low spot on a track: ground vehicles crossing it can bog down.
struct Hazard {
  std::string id;
  double x = 0, y = 0, r = 3.5;
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
  std::map<std::string, Section> sections;
  std::vector<Hazard> hazards;
  std::map<std::string, RobotRef> robots;
  std::map<std::string, Infra> infra;
  bool loaded = false;
};

std::string stateName(CropState s);
CropState stateFrom(const std::string &s);

}  // namespace arcadia
