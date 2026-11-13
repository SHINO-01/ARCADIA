// Agronomy ground truth + proximity-based mock computer vision.
#include "arcadia_supervisor/ground_truth.hpp"

#include <cmath>

namespace arcadia {

namespace {
constexpr double SEEDED_S = 70.0;
constexpr double GROWING_S = 110.0;
constexpr double HARVESTED_S = 40.0;
}  // namespace

void GroundTruth::seed(unsigned s) { rng_.seed(s); }

void GroundTruth::initialise() {
  std::uniform_real_distribution<double> u(0.0, 1.0);
  std::normal_distribution<double> n(0.0, 0.08);
  for (auto &[h3, c] : w_.cells) {
    if (c.kind != "crop") continue;
    c.health = 1.0;
    c.t_state = 0.0;
    c.moisture = std::clamp(c.moisture + n(rng_), 0.05, 1.0);
    spawner_.setWeeds(c, u(rng_) < 0.12);
    spawner_.setCropState(c);
  }
}

void GroundTruth::grow(double dt) {
  std::uniform_real_distribution<double> u(0.0, 1.0);
  for (auto &[h3, c] : w_.cells) {
    if (c.kind != "crop") continue;
    c.t_state += dt;
    bool changed = false;
    if (c.state == CropState::Seeded && c.t_state > SEEDED_S) { c.state = CropState::Growing; c.t_state = 0; changed = true; }
    else if (c.state == CropState::Growing && c.t_state > GROWING_S * (1.3 - 0.3 * c.moisture)) { c.state = CropState::Ripe; c.t_state = 0; changed = true; }
    else if (c.state == CropState::Harvested && c.t_state > HARVESTED_S) { c.state = CropState::Fallow; c.t_state = 0; changed = true; }
    if (!c.weeds && u(rng_) < 0.0035 * dt) spawner_.setWeeds(c, true);
    c.moisture = std::max(0.02, c.moisture - 0.00025 * dt);
    c.health = c.weeds ? std::max(0.2, c.health - 0.0006 * dt) : std::min(1.0, c.health + 0.0003 * dt);
    if (changed) spawner_.setCropState(c);
  }
}

void GroundTruth::observe(const RobotRef &uav, double radius) {
  if (!uav.node) return;
  const double *p = uav.node->getPosition();
  const double r2 = radius * radius;
  for (auto &[h3, c] : w_.cells) {
    if (c.kind != "crop") continue;
    double dx = c.x - p[0], dy = c.y - p[1];
    if (dx * dx + dy * dy > r2) continue;
    // "multispectral" signature: only emit when something the backend doesn't know changed
    char sig[64];
    std::snprintf(sig, sizeof sig, "%s|%d|%.1f|%.1f", stateName(c.state).c_str(), c.weeds ? 1 : 0, c.moisture, c.health);
    if (c.observed_sig == sig) continue;
    c.observed_sig = sig;
    emit_("cell_observed", uav.id, h3, "INFO",
          json{{"state", stateName(c.state)}, {"weeds", c.weeds}, {"soil_moisture", std::round(c.moisture * 1000) / 1000},
               {"health", std::round(c.health * 1000) / 1000}});
  }
}

void GroundTruth::onRobotEvent(const std::string &kind, const std::string &h3, const json &payload) {
  auto it = w_.cells.find(h3);
  if (it == w_.cells.end()) return;
  Cell &c = it->second;
  if (kind == "seeded") { c.state = CropState::Seeded; c.t_state = 0; spawner_.setCropState(c); }
  else if (kind == "weeded") { spawner_.setWeeds(c, false); c.health = std::min(1.0, c.health + 0.3); }
  else if (kind == "harvested") { c.state = CropState::Harvested; c.t_state = 0; spawner_.setCropState(c); }
  (void)payload;
}

}  // namespace arcadia
