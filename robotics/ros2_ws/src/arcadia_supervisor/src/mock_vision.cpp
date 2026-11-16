// Proximity-based mock CV: section_seen events when the scout UAV overflies a bed section.
#include "arcadia_supervisor/mock_vision.hpp"

#include <cmath>

namespace arcadia {

void MockVision::scan(const RobotRef &uav, double radius, double now, double cooldown) {
  if (!uav.node) return;
  const double *p = uav.node->getPosition();
  for (auto &[id, c] : w_.sections) {
    const double reach = radius + c.length / 2;
    const double dx = c.x - p[0], dy = c.y - p[1];
    const double d2 = dx * dx + dy * dy;
    if (d2 > reach * reach || now - c.seen_t < cooldown) continue;
    c.seen_t = now;
    emit_("section_seen", uav.id, id, "INFO",
          json{{"section", id}, {"distance_m", std::round(std::sqrt(d2) * 10) / 10}, {"altitude_m", std::round(p[2] * 10) / 10}});
  }
}

}  // namespace arcadia
