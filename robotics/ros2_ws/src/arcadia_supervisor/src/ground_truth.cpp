// Agronomy ground truth per bed section + proximity-based mock computer vision.
#include "arcadia_supervisor/ground_truth.hpp"

#include <cmath>

namespace arcadia {

namespace {
constexpr double SEEDED_S = 70.0;
constexpr double GROWING_S = 110.0;
constexpr double HARVESTED_S = 40.0;
// New weed patches per section per second: about one every two minutes on a 450-section field,
// which a single crop-care pass can keep up with (it was ~1/s, so the UGV never got to sow).
constexpr double WEED_RATE = 0.00002;
}  // namespace

void GroundTruth::seed(unsigned s) { rng_.seed(s); }

void GroundTruth::initialise() {
  std::uniform_real_distribution<double> u(0.0, 1.0);
  std::normal_distribution<double> n(0.0, 0.06);
  for (auto &[id, c] : w_.sections) {
    c.health = 1.0;
    c.t_state = 0.0;
    c.moisture = std::clamp(c.moisture + n(rng_), 0.05, 1.0);
    spawner_.setWeeds(c, u(rng_) < 0.10);
    spawner_.setCropState(c);
  }
}

void GroundTruth::grow(double dt) {
  std::uniform_real_distribution<double> u(0.0, 1.0);
  for (auto &[id, c] : w_.sections) {
    c.t_state += dt;
    bool changed = false;
    if (c.state == CropState::Seeded && c.t_state > SEEDED_S) { c.state = CropState::Growing; c.t_state = 0; changed = true; }
    else if (c.state == CropState::Growing && c.t_state > GROWING_S * (1.3 - 0.3 * c.moisture)) { c.state = CropState::Ripe; c.t_state = 0; changed = true; }
    else if (c.state == CropState::Harvested && c.t_state > HARVESTED_S) { c.state = CropState::Fallow; c.t_state = 0; changed = true; }
    if (!c.weeds && u(rng_) < WEED_RATE * dt) spawner_.setWeeds(c, true);
    c.moisture = std::max(0.02, c.moisture - 0.00025 * dt);
    c.health = c.weeds ? std::max(0.2, c.health - 0.0006 * dt) : std::min(1.0, c.health + 0.0003 * dt);
    if (changed) spawner_.setCropState(c);
  }
}

void GroundTruth::observe(const RobotRef &uav, double radius) {
  if (!uav.node) return;
  const double *p = uav.node->getPosition();
  for (auto &[id, c] : w_.sections) {
    const double reach = radius + c.length / 2;
    const double dx = c.x - p[0], dy = c.y - p[1];
    if (dx * dx + dy * dy > reach * reach) continue;
    // "multispectral" signature: only report what changed since the last pass
    char sig[64];
    std::snprintf(sig, sizeof sig, "%s|%d|%.1f|%.1f", stateName(c.state).c_str(), c.weeds ? 1 : 0, c.moisture, c.health);
    if (c.observed_sig == sig) continue;
    c.observed_sig = sig;
    emit_("section_observed", uav.id, "", "INFO",
          json{{"section", id}, {"state", stateName(c.state)}, {"weeds", c.weeds},
               {"moisture", std::round(c.moisture * 1000) / 1000}, {"health", std::round(c.health * 1000) / 1000}});
  }
}

void GroundTruth::onRobotEvent(const std::string &kind, const std::string &robot, const std::string &section, const json &payload) {
  auto it = w_.sections.find(section);
  if (it == w_.sections.end()) return;
  Section &c = it->second;
  if (kind == "seeded") {  // the drill cultivates as it sows: weeds go with it
    c.state = CropState::Seeded; c.t_state = 0; spawner_.setCropState(c); spawner_.setWeeds(c, false);
  }
  else if (kind == "weeded") { spawner_.setWeeds(c, false); c.health = std::min(1.0, c.health + 0.3); }
  else if (kind == "harvested") {
    // Only the ground truth knows whether the crop was ripe; an early cut clears the bed for nothing.
    const std::string was = stateName(c.state);
    emit_("harvest_result", robot, section, "INFO", json{{"section", section}, {"crop", c.crop}, {"was", was},
                                                        {"area_m2", std::round(c.length * c.width * 10) / 10}});
    c.state = CropState::Harvested; c.t_state = 0; spawner_.setCropState(c);
  }
  (void)payload;
}

}  // namespace arcadia
