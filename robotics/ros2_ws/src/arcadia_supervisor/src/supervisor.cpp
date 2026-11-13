// ARCADIA Webots Supervisor (extern controller + rclcpp node).
//
//  * ingests `world_load` Commands (JSON world config from the backend) and procedurally spawns
//    terrain, hex crops, renewable docks, storage and the robot fleet from primitives
//  * owns crop ground truth and runs the proximity-based mock CV (cell_observed events)
//  * reacts to robot work events (seeded / weeded / harvested) by mutating the crops
//  * implements tow mechanics: a towed robot is dragged behind the Logistics AGV and re-parked
//  * broadcasts solar irradiance (compressed day cycle) so solar skins trickle-charge
#include <chrono>
#include <cmath>
#include <memory>
#include <string>

#include <nlohmann/json.hpp>
#include <rclcpp/rclcpp.hpp>
#include <webots/Supervisor.hpp>

#include "arcadia_msgs/msg/command.hpp"
#include "arcadia_msgs/msg/event.hpp"
#include "arcadia_supervisor/ground_truth.hpp"
#include "arcadia_supervisor/world_spawner.hpp"
#include "arcadia_supervisor/world_state.hpp"

using arcadia::json;
using arcadia_msgs::msg::Command;
using arcadia_msgs::msg::Event;

class ArcadiaSupervisor : public rclcpp::Node {
 public:
  ArcadiaSupervisor()
      : Node("arcadia_supervisor"), sup_(), spawner_(sup_),
        truth_(world_, spawner_, [this](auto &&...a) { emit(std::forward<decltype(a)>(a)...); }) {
    dt_ = sup_.getBasicTimeStep() / 1000.0;
    pub_event_ = create_publisher<Event>("/arcadia/event", 50);
    pub_cmd_ = create_publisher<Command>("/arcadia/command", 10);
    sub_cmd_ = create_subscription<Command>("/arcadia/command", 50, [this](Command::SharedPtr m) { onCommand(*m); });
    sub_evt_ = create_subscription<Event>("/arcadia/event", 50, [this](Event::SharedPtr m) { onEvent(*m); });
    RCLCPP_INFO(get_logger(), "ARCADIA supervisor online (dt=%.0f ms). Waiting for world_load...", dt_ * 1000);
  }

  void run() {
    double t_irr = 0;
    while (sup_.step(int(dt_ * 1000)) != -1 && rclcpp::ok()) {
      rclcpp::spin_some(get_node_base_interface());
      if (!world_.loaded) continue;
      sim_t_ += dt_;
      truth_.grow(dt_);
      for (auto &[id, r] : world_.robots) {
        if (!r.node) continue;
        if (r.type == "scout_uav") {
          const double *p = r.node->getPosition();
          if (p[2] > 1.5) truth_.observe(r, sensorRadius(r));
        }
        if (!r.towing.empty() && r.tow_attached) dragTowed(r);
      }
      t_irr += dt_;
      if (t_irr > 5.0) { t_irr = 0; broadcastIrradiance(); }
    }
  }

 private:
  webots::Supervisor sup_;
  arcadia::WorldSpawner spawner_;
  arcadia::World world_;
  arcadia::GroundTruth truth_;
  double dt_ = 0.032, sim_t_ = 0;
  rclcpp::Publisher<Event>::SharedPtr pub_event_;
  rclcpp::Publisher<Command>::SharedPtr pub_cmd_;
  rclcpp::Subscription<Command>::SharedPtr sub_cmd_;
  rclcpp::Subscription<Event>::SharedPtr sub_evt_;

  double sensorRadius(const arcadia::RobotRef &r) {
    try { for (const auto &rc : world_.config["robots"]) if (rc["id"] == r.id) return rc["spec"].value("sensor_radius_m", 9.0); }
    catch (...) {}
    return 9.0;
  }

  void emit(const std::string &kind, const std::string &robot, const std::string &h3, const std::string &severity, const json &payload) {
    Event e;
    e.stamp = now();
    e.kind = kind; e.robot_id = robot; e.h3 = h3; e.severity = severity; e.payload_json = payload.dump();
    pub_event_->publish(e);
  }

  void onCommand(const Command &c) {
    if (c.kind == "world_load") {
      try {
        json p = json::parse(c.payload_json);
        const json &cfg = p.contains("world") ? p["world"] : p;
        if (world_.loaded && world_.farm_id == cfg["farm"]["id"].get<std::string>()) {
          RCLCPP_INFO(get_logger(), "world_load for the already-loaded farm; ignoring");
          return;
        }
        RCLCPP_INFO(get_logger(), "Spawning farm '%s': %zu cells, %zu infra, %zu robots",
                    cfg["farm"]["name"].get<std::string>().c_str(), cfg["hex"]["cells"].size(), cfg["infrastructure"].size(), cfg["robots"].size());
        spawner_.load(cfg, world_);
        truth_.seed(cfg["farm"].value("seed", 42));
        truth_.initialise();
        sup_.step(int(dt_ * 1000));  // let the scene settle before controllers connect
        emit("world_loaded", "", "", "INFO", json{{"source", "webots"}, {"cells", world_.cells.size()}, {"farm_id", world_.farm_id}});
        broadcastIrradiance();
      } catch (const std::exception &e) {
        RCLCPP_ERROR(get_logger(), "world_load failed: %s", e.what());
      }
      return;
    }
    if (c.kind == "set_active") {
      auto it = world_.robots.find(c.robot_id);
      if (it != world_.robots.end()) {
        try {
          json p = json::parse(c.payload_json);
          it->second.active = p.value("active", true);
          if (!it->second.active) parkAtHome(it->second);
        } catch (...) {}
      }
    }
  }

  void onEvent(const Event &e) {
    json p;
    try { p = e.payload_json.empty() ? json::object() : json::parse(e.payload_json); } catch (...) { p = json::object(); }
    if (e.kind == "seeded" || e.kind == "weeded" || e.kind == "harvested") {
      truth_.onRobotEvent(e.kind, e.h3, p);
    } else if (e.kind == "tow_attached") {
      auto it = world_.robots.find(e.robot_id);
      if (it != world_.robots.end()) { it->second.towing = p.value("target", ""); it->second.tow_attached = true; }
    } else if (e.kind == "tow_complete") {
      auto it = world_.robots.find(e.robot_id);
      if (it != world_.robots.end()) {
        auto tgt = world_.robots.find(it->second.towing);
        if (tgt != world_.robots.end()) parkAtHome(tgt->second);
        it->second.towing.clear(); it->second.tow_attached = false;
      }
    } else if (e.kind == "fault_cleared" && p.value("recharged", false)) {
      auto it = world_.robots.find(e.robot_id);
      if (it != world_.robots.end()) parkAtHome(it->second);
    }
  }

  void dragTowed(arcadia::RobotRef &agv) {
    auto tgt = world_.robots.find(agv.towing);
    if (tgt == world_.robots.end() || !tgt->second.node || !agv.node) return;
    const double *p = agv.node->getPosition();
    const double *o = agv.node->getOrientation();  // 3x3 row-major; heading = atan2(o[3], o[0])
    double yaw = std::atan2(o[3], o[0]);
    const double tr[3] = {p[0] - 2.1 * std::cos(yaw), p[1] - 2.1 * std::sin(yaw), 0.25};
    tgt->second.node->getField("translation")->setSFVec3f(tr);
    const double rot[4] = {0, 0, 1, yaw};
    tgt->second.node->getField("rotation")->setSFRotation(rot);
    tgt->second.node->resetPhysics();
  }

  void parkAtHome(arcadia::RobotRef &r) {
    auto home = world_.infra.find(r.home);
    if (home == world_.infra.end() || !r.node) return;
    // ring slot around the dock pad so parked units never overlap
    size_t idx = std::distance(world_.robots.begin(), world_.robots.find(r.id));
    double ang = idx * 2.0943951;
    const double tr[3] = {home->second.x + 2.2 * std::cos(ang), home->second.y + 2.2 * std::sin(ang), r.type == "scout_uav" ? 0.3 : 0.25};
    r.node->getField("translation")->setSFVec3f(tr);
    r.node->resetPhysics();
  }

  void broadcastIrradiance() {
    // compressed day: 600 s wall-clock per 24 h, matching the backend microgrid model
    double hour = std::fmod(std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(), 600.0) / 600.0 * 24.0;
    double irr = (hour >= 6 && hour <= 18) ? std::max(0.0, std::sin(M_PI * (hour - 6) / 12.0)) : 0.0;
    for (auto &[id, r] : world_.robots) {
      Command c;
      c.command_id = "sup"; c.robot_id = id; c.kind = "set_irradiance";
      c.payload_json = json{{"irradiance", std::round(irr * 100) / 100}, {"sim_hour", std::round(hour * 10) / 10}}.dump();
      pub_cmd_->publish(c);
    }
  }
};

int main(int argc, char **argv) {
  rclcpp::init(argc, argv);
  auto node = std::make_shared<ArcadiaSupervisor>();
  node->run();
  rclcpp::shutdown();
  return 0;
}
