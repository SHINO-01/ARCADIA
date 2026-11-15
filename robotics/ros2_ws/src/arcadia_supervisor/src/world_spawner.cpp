// Procedural farm spawner. Everything is a primitive (Plane / Cylinder / Box / Sphere) so the
// scene stays light on integrated graphics: ~300 crop cylinders + a handful of boxes.
#include "arcadia_supervisor/world_spawner.hpp"

#include <sstream>
#include <webots/Field.hpp>

namespace arcadia {

std::string stateName(CropState s) {
  switch (s) {
    case CropState::Fallow: return "fallow";
    case CropState::Seeded: return "seeded";
    case CropState::Growing: return "growing";
    case CropState::Ripe: return "ripe";
    case CropState::Harvested: return "harvested";
    default: return "infra";
  }
}
CropState stateFrom(const std::string &s) {
  if (s == "seeded") return CropState::Seeded;
  if (s == "growing") return CropState::Growing;
  if (s == "ripe") return CropState::Ripe;
  if (s == "harvested") return CropState::Harvested;
  if (s == "infra") return CropState::Infra;
  return CropState::Fallow;
}

namespace {
struct Rgb { double r, g, b; };
Rgb cropColour(const std::string &crop, CropState st) {
  Rgb base{0.35, 0.65, 0.3};
  if (crop == "wheat") base = {0.75, 0.68, 0.35};
  else if (crop == "maize") base = {0.4, 0.7, 0.3};
  else if (crop == "soy") base = {0.45, 0.72, 0.4};
  else if (crop == "tomato") base = {0.35, 0.6, 0.3};
  else if (crop == "lettuce") base = {0.5, 0.85, 0.45};
  const Rgb soil{0.30, 0.22, 0.14};
  auto mix = [](Rgb a, Rgb b, double k) { return Rgb{a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k}; };
  switch (st) {
    case CropState::Seeded: return mix(soil, base, 0.45);
    case CropState::Growing: return base;
    case CropState::Ripe: return {std::min(1.0, base.r + 0.3), base.g, base.b * 0.6};
    default: return mix(soil, base, 0.30);  // tilled bed, tinted by its crop
  }
}
double stateHeight(double full, CropState st) {
  switch (st) {
    case CropState::Seeded: return 0.12;
    case CropState::Growing: return full * 0.6;
    case CropState::Ripe: return full;
    default: return 0.06;  // flat bed tile, clearly above the ground plane
  }
}
std::string f(double v) { std::ostringstream o; o.precision(4); o << std::fixed << v; return o.str(); }
}  // namespace

webots::Field *WorldSpawner::farmChildren() {
  webots::Node *farm = sup_.getFromDef("FARM");
  return farm ? farm->getField("children") : nullptr;
}

void WorldSpawner::clear(World &world) {
  if (webots::Field *ch = farmChildren()) {
    while (ch->getCount() > 0) ch->removeMF(0);
  }
  world.cells.clear();
  world.robots.clear();
  world.infra.clear();
  world.loaded = false;
  imported_ = 0;
}

void WorldSpawner::load(const json &cfg, World &world) {
  clear(world);
  world.config = cfg;
  world.farm_id = cfg["farm"]["id"].get<std::string>();
  world.edge_m = cfg["hex"].value("edge_m", 10.0);
  spawnTerrain(cfg);
  spawnInfra(cfg, world);
  spawnCells(cfg, world);
  spawnRobots(cfg, world);
  world.loaded = true;
}

void WorldSpawner::spawnTerrain(const json &cfg) {
  const auto &t = cfg["terrain"];
  double w = t.value("width", 300.0), h = t.value("height", 300.0);
  double cx = (t.value("min_x", -150.0) + t.value("max_x", 150.0)) / 2.0;
  double cy = (t.value("min_y", -150.0) + t.value("max_y", 150.0)) / 2.0;
  // tilled field plane slightly above the ground to avoid z-fighting
  std::ostringstream s;
  s << "Solid { translation " << f(cx) << " " << f(cy) << " 0.005 name \"field\" children [ Shape { appearance PBRAppearance { baseColor 0.42 0.31 0.18 roughness 1 metalness 0 } geometry Plane { size " << f(w) << " " << f(h) << " } } ] }";
  farmChildren()->importMFNodeFromString(-1, s.str());
  // boundary fence posts (one every ~15 m) - cheap boxes
  int nx = std::max(2, int(w / 15)), ny = std::max(2, int(h / 15));
  std::ostringstream g;
  g << "Solid { name \"fence\" children [";
  for (int i = 0; i <= nx; ++i) {
    double x = t.value("min_x", -150.0) + w * i / nx;
    g << " Pose { translation " << f(x) << " " << f(t.value("min_y", -150.0)) << " 0.6 children [ Shape { appearance PBRAppearance { baseColor 0.5 0.45 0.4 } geometry Box { size 0.12 0.12 1.2 } } ] }";
    g << " Pose { translation " << f(x) << " " << f(t.value("max_y", 150.0)) << " 0.6 children [ Shape { appearance PBRAppearance { baseColor 0.5 0.45 0.4 } geometry Box { size 0.12 0.12 1.2 } } ] }";
  }
  for (int j = 1; j < ny; ++j) {
    double y = t.value("min_y", -150.0) + h * j / ny;
    g << " Pose { translation " << f(t.value("min_x", -150.0)) << " " << f(y) << " 0.6 children [ Shape { appearance PBRAppearance { baseColor 0.5 0.45 0.4 } geometry Box { size 0.12 0.12 1.2 } } ] }";
    g << " Pose { translation " << f(t.value("max_x", 150.0)) << " " << f(y) << " 0.6 children [ Shape { appearance PBRAppearance { baseColor 0.5 0.45 0.4 } geometry Box { size 0.12 0.12 1.2 } } ] }";
  }
  g << " ] }";
  farmChildren()->importMFNodeFromString(-1, g.str());
}

void WorldSpawner::spawnCells(const json &cfg, World &world) {
  webots::Field *ch = farmChildren();
  double edge = world.edge_m;
  double radius = std::max(0.25, edge * 0.82);  // hexagonal bed tile / crop block (small gap shows the hex grid)
  for (const auto &c : cfg["hex"]["cells"]) {
    Cell cell;
    cell.h3 = c["h3"].get<std::string>();
    cell.x = c["x"].get<double>();
    cell.y = c["y"].get<double>();
    cell.kind = c.value("kind", "crop");
    cell.moisture = c.value("moisture", 0.5);
    cell.waterlogged = c.value("waterlogged", false);
    if (cell.kind == "road") {
      // travel road: flat grey hex-sized disc (cheap), slightly above the field plane
      std::ostringstream rs;
      rs << "Solid { translation " << f(cell.x) << " " << f(cell.y) << " 0.025 name \"road_" << cell.h3
         << "\" children [ Shape { appearance PBRAppearance { baseColor 0.66 0.64 0.6 roughness 1 metalness 0 } geometry Cylinder { radius "
         << f(edge * 0.98) << " height 0.05 subdivision 6 } } ] }";
      ch->importMFNodeFromString(-1, rs.str());
      cell.state = CropState::Infra;
      world.cells[cell.h3] = cell;
      continue;
    }
    if (cell.kind != "crop") {
      cell.state = CropState::Infra;
      world.cells[cell.h3] = cell;
      continue;
    }
    if (cell.waterlogged) {
      // puddle marker so operators can see the hazard in the 3D view
      std::ostringstream ws;
      ws << "Solid { translation " << f(cell.x) << " " << f(cell.y) << " 0.075 name \"water_" << cell.h3
         << "\" children [ Shape { appearance PBRAppearance { baseColor 0.25 0.45 0.7 roughness 0.05 metalness 0.2 transparency 0.2 } geometry Cylinder { radius "
         << f(edge * 0.6) << " height 0.02 subdivision 8 } } ] }";
      ch->importMFNodeFromString(-1, ws.str());
    }
    cell.crop = c.value("crop", "wheat");
    cell.crop_height = c.value("crop_height", 0.8);
    cell.state = stateFrom(c.value("state", "fallow"));
    Rgb col = cropColour(cell.crop, cell.state);
    double hgt = stateHeight(cell.crop_height, cell.state);
    std::ostringstream s;
    s << "Solid { translation " << f(cell.x) << " " << f(cell.y) << " " << f(hgt / 2) << " name \"crop_" << cell.h3
      << "\" children [ Shape { appearance PBRAppearance { baseColor " << f(col.r) << " " << f(col.g) << " " << f(col.b)
      << " roughness 1 metalness 0 } geometry Cylinder { radius " << f(radius) << " height " << f(hgt) << " subdivision 6 } } ] }";
    ch->importMFNodeFromString(-1, s.str());
    cell.node = ch->getMFNode(ch->getCount() - 1);
    // weed marker: small red sphere, toggled by scaling via translation below ground when clean
    std::ostringstream wm;
    wm << "Solid { translation " << f(cell.x) << " " << f(cell.y) << " -2 name \"weed_" << cell.h3
       << "\" children [ Shape { appearance PBRAppearance { baseColor 0.95 0.2 0.25 roughness 0.8 emissiveColor 0.4 0 0 } geometry Sphere { radius "
       << f(std::max(0.4, edge * 0.12)) << " subdivision 1 } } ] }";
    ch->importMFNodeFromString(-1, wm.str());
    cell.weed_node = ch->getMFNode(ch->getCount() - 1);
    world.cells[cell.h3] = cell;
  }
}

void WorldSpawner::setCropState(Cell &c) {
  if (!c.node) return;
  Rgb col = cropColour(c.crop, c.state);
  double hgt = stateHeight(c.crop_height, c.state);
  // Solid > children[0] (Shape) > geometry (Cylinder) / appearance
  webots::Node *shape = c.node->getField("children")->getMFNode(0);
  if (shape) {
    if (webots::Node *geo = shape->getField("geometry")->getSFNode()) geo->getField("height")->setSFFloat(hgt);
    if (webots::Node *app = shape->getField("appearance")->getSFNode()) {
      const double rgb[3] = {col.r, col.g, col.b};
      app->getField("baseColor")->setSFColor(rgb);
    }
  }
  const double tr[3] = {c.x, c.y, hgt / 2};
  c.node->getField("translation")->setSFVec3f(tr);
  if (c.weeds) setWeeds(c, true);  // keep the weed marker on top of the (re)grown crop
}

void WorldSpawner::setWeeds(Cell &c, bool on) {
  c.weeds = on;
  if (!c.weed_node) return;
  const double top = stateHeight(c.crop_height, c.state);
  const double tr[3] = {c.x, c.y, on ? top + 0.45 : -2.0};
  c.weed_node->getField("translation")->setSFVec3f(tr);
}

void WorldSpawner::spawnInfra(const json &cfg, World &world) {
  webots::Field *ch = farmChildren();
  for (const auto &i : cfg["infrastructure"]) {
    Infra in;
    in.id = i["id"].get<std::string>();
    in.kind = i["kind"].get<std::string>();
    in.x = i["x"].get<double>();
    in.y = i["y"].get<double>();
    std::ostringstream s;
    s << "Solid { translation " << f(in.x) << " " << f(in.y) << " 0 name \"" << in.id << "\" children [";
    // charging pad
    s << " Pose { translation 0 0 0.05 children [ Shape { appearance PBRAppearance { baseColor 0.2 0.22 0.24 roughness 0.6 metalness 0.5 } geometry Box { size 6 6 0.1 } } ] }";
    if (in.kind == "solar_dock") {
      // tilted solar array on a mast
      s << " Pose { translation -2.2 0 1.4 children [ Shape { appearance PBRAppearance { baseColor 0.3 0.3 0.32 } geometry Cylinder { radius 0.12 height 2.8 } } ] }";
      s << " Pose { translation -2.2 0 3.0 rotation 0 1 0 -0.5 children [ Shape { appearance PBRAppearance { baseColor 0.1 0.18 0.45 metalness 0.8 roughness 0.15 } geometry Box { size 3.2 4.2 0.06 } } ] }";
    } else if (in.kind == "wind_dock") {
      // wind turbine mast + nacelle + 3 blades
      s << " Pose { translation -2.2 0 4 children [ Shape { appearance PBRAppearance { baseColor 0.9 0.9 0.92 } geometry Cylinder { radius 0.15 height 8 } } ] }";
      s << " Pose { translation -2.2 0 8.1 children [ Shape { appearance PBRAppearance { baseColor 0.85 0.85 0.88 } geometry Box { size 0.6 0.5 0.5 } } ] }";
      for (int b = 0; b < 3; ++b) {
        double a = b * 2.0943951;
        s << " Pose { translation -2.55 0 8.1 rotation 1 0 0 " << f(a) << " children [ Pose { translation 0 0 1.5 children [ Shape { appearance PBRAppearance { baseColor 0.95 0.95 0.97 } geometry Box { size 0.08 0.3 3.0 } } ] } ] }";
      }
    } else if (in.kind == "repair_station") {
      // repair bay: open-sided canopy on four posts, tool bench, orange trim
      s << " Pose { translation 0 0 3.2 children [ Shape { appearance PBRAppearance { baseColor 0.95 0.55 0.2 roughness 0.6 } geometry Box { size 7 6 0.2 } } ] }";
      for (double px : {-3.2, 3.2}) for (double py : {-2.7, 2.7})
        s << " Pose { translation " << f(px) << " " << f(py) << " 1.6 children [ Shape { appearance PBRAppearance { baseColor 0.3 0.3 0.32 metalness 0.8 } geometry Cylinder { radius 0.12 height 3.2 } } ] }";
      s << " Pose { translation -2.6 0 0.5 children [ Shape { appearance PBRAppearance { baseColor 0.25 0.25 0.27 roughness 0.9 } geometry Box { size 0.8 4 0.9 } } ] }";
    } else {
      // robot storage facility / silo
      s << " Pose { translation 0 0 1.6 children [ Shape { appearance PBRAppearance { baseColor 0.55 0.5 0.7 roughness 0.7 } geometry Box { size 7 5 3.2 } } ] }";
      s << " Pose { translation 4.5 0 2.5 children [ Shape { appearance PBRAppearance { baseColor 0.75 0.75 0.78 metalness 0.6 } geometry Cylinder { radius 1.2 height 5 } } ] }";
    }
    // battery bank
    s << " Pose { translation 2.4 2.2 0.5 children [ Shape { appearance PBRAppearance { baseColor 0.15 0.6 0.35 roughness 0.5 } geometry Box { size 1.0 1.2 0.9 } } ] }";
    s << " ] }";
    ch->importMFNodeFromString(-1, s.str());
    in.node = ch->getMFNode(ch->getCount() - 1);
    world.infra[in.id] = in;
  }
}

void WorldSpawner::spawnRobots(const json &cfg, World &world) {
  webots::Field *ch = farmChildren();
  for (const auto &r : cfg["robots"]) {
    RobotRef ref;
    ref.id = r["id"].get<std::string>();
    ref.type = r["type"].get<std::string>();
    ref.home = r.value("home", "dock-1");
    ref.active = r.value("active", true);
    double x = r["x"].get<double>(), y = r["y"].get<double>();
    std::ostringstream s;
    if (ref.type == "scout_uav") {
      s << "ArcadiaUAV { translation " << f(x) << " " << f(y) << " 0.3 name \"" << ref.id << "\" }";
    } else if (ref.type == "logistics_agv") {
      s << "ArcadiaAGV { translation " << f(x) << " " << f(y) << " 0.25 name \"" << ref.id << "\" }";
    } else {
      std::string col = ref.type == "seeder_ugv" ? "0.96 0.77 0.32" : ref.type == "cropcare_ugv" ? "0.49 0.89 0.55" : "1 0.62 0.42";
      s << "ArcadiaUGV { translation " << f(x) << " " << f(y) << " 0.25 name \"" << ref.id << "\" customData \"" << ref.type
        << "\" color " << col << " }";
    }
    ch->importMFNodeFromString(-1, s.str());
    ref.node = ch->getMFNode(ch->getCount() - 1);
    world.robots[ref.id] = ref;
  }
}

}  // namespace arcadia
