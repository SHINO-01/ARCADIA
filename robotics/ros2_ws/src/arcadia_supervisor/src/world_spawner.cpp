// Procedural farm spawner. Everything is a primitive (Plane / Cylinder / Box / Sphere) so the
// scene stays light on integrated graphics: ~300 crop cylinders + a handful of boxes.
#include "arcadia_supervisor/world_spawner.hpp"

#include <sstream>
#include <vector>
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
    default: return mix(soil, base, 0.50);  // tilled bed, tinted by its crop
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
  world.sections.clear();
  world.hazards.clear();
  world.robots.clear();
  world.infra.clear();
  world.loaded = false;
  imported_ = 0;
}

void WorldSpawner::load(const json &cfg, World &world) {
  clear(world);
  world.config = cfg;
  world.farm_id = cfg["farm"]["id"].get<std::string>();
  spawnTerrain(cfg);
  spawnInfra(cfg, world);
  spawnFieldLayout(cfg, world);
  spawnRobots(cfg, world);
  world.loaded = true;
}

void WorldSpawner::spawnTerrain(const json &cfg) {
  const auto &t = cfg["terrain"];
  double w = t.value("width", 300.0), h = t.value("height", 300.0);
  // One ground plane (two coplanar planes flicker in the web viewer): unworked margins are dry grass.
  if (webots::Node *ground = sup_.getFromDef("GROUND")) {
    webots::Node *shape = ground->getField("children")->getMFNode(0);
    if (shape) {
      if (webots::Node *app = shape->getField("appearance")->getSFNode()) {
        const double grass[3] = {0.47, 0.45, 0.29};
        app->getField("baseColor")->setSFColor(grass);
      }
      if (webots::Node *geo = shape->getField("geometry")->getSFNode()) {
        const double size[2] = {std::max(600.0, w * 1.6), std::max(600.0, h * 1.6)};
        geo->getField("size")->setSFVec2f(size);
      }
    }
  }
  // Depth precision for the overview camera (the browser viewer has a shallow depth buffer).
  if (webots::Node *vp = sup_.getFromDef("VIEWPOINT")) vp->getField("near")->setSFFloat(2.0);
  // Fence posts along the real field boundary, one every ~12 m.
  std::ostringstream g;
  g << "Solid { name \"fence\" children [";
  if (t.contains("boundary") && t["boundary"].size() >= 3) {
    const auto &b = t["boundary"];
    for (size_t i = 0; i + 1 < b.size(); ++i) {
      double x0 = b[i][0], y0 = b[i][1], x1 = b[i + 1][0], y1 = b[i + 1][1];
      int n = std::max(1, int(std::hypot(x1 - x0, y1 - y0) / 12.0));
      for (int k = 0; k < n; ++k) {
        double x = x0 + (x1 - x0) * k / n, y = y0 + (y1 - y0) * k / n;
        g << " Pose { translation " << f(x) << " " << f(y) << " 0.6 children [ Shape { appearance PBRAppearance { baseColor 0.45 0.38 0.3 } geometry Box { size 0.14 0.14 1.2 } } ] }";
      }
    }
  }
  g << " ] }";
  farmChildren()->importMFNodeFromString(-1, g.str());
}

void WorldSpawner::spawnFieldLayout(const json &cfg, World &world) {
  // Batched spawn: one Group import each for tracks, hazards, beds and weed markers. Each import is
  // a scene-tree rebuild plus a W3D update to every streaming client, so batching keeps a 500-bed
  // farm to a couple of seconds. Bed and weed nodes are looked up by index afterwards.
  webots::Field *ch = farmChildren();
  std::ostringstream tracks, water, beds, weeds;
  tracks << "Group { children [";
  water << "Group { children [";
  beds << "Group { children [";
  weeds << "Group { children [";

  // Tracks: compacted dirt strips (one thin box per polyline leg + a disc at every bend).
  for (const auto &t : cfg.value("tracks", json::array())) {
    const std::string kind = t.value("kind", "lane");
    const double w = t.value("width", 2.4);
    const char *col = kind == "lane" ? "0.6 0.5 0.36" : kind == "spur" ? "0.64 0.62 0.58" : "0.66 0.56 0.42";
    const auto &c = t["coords"];
    for (size_t i = 0; i + 1 < c.size(); ++i) {
      double x0 = c[i][0], y0 = c[i][1], x1 = c[i + 1][0], y1 = c[i + 1][1];
      double len = std::hypot(x1 - x0, y1 - y0);
      if (len < 0.05) continue;
      tracks << " Pose { translation " << f((x0 + x1) / 2) << " " << f((y0 + y1) / 2) << " 0.02 rotation 0 0 1 "
             << f(std::atan2(y1 - y0, x1 - x0)) << " children [ Shape { appearance PBRAppearance { baseColor " << col
             << " roughness 1 metalness 0 } geometry Box { size " << f(len) << " " << f(w) << " 0.04 } } ] }";
      if (i > 0 && kind != "lane")
        tracks << " Pose { translation " << f(x0) << " " << f(y0) << " 0.02 children [ Shape { appearance PBRAppearance { baseColor "
               << col << " roughness 1 metalness 0 } geometry Cylinder { radius " << f(w / 2) << " height 0.04 subdivision 12 } } ] }";
    }
  }
  // Waterlogged low spots on the tracks.
  for (const auto &hz : cfg.value("hazards", json::array())) {
    Hazard h;
    h.id = hz.value("id", "wet");
    h.x = hz["x"];
    h.y = hz["y"];
    h.r = hz.value("r", 3.5);
    world.hazards.push_back(h);
    water << " Pose { translation " << f(h.x) << " " << f(h.y) << " 0.05 children [ Shape { appearance PBRAppearance { baseColor 0.25 0.42 0.62 roughness 0.05 metalness 0.2 transparency 0.15 } geometry Cylinder { radius "
          << f(h.r) << " height 0.02 subdivision 16 } } ] }";
  }
  // Beds: one box per section, tinted by crop, growing with the crop.
  std::vector<std::string> order;
  for (const auto &c : cfg.value("sections", json::array())) {
    Section sec;
    sec.id = c["id"].get<std::string>();
    sec.bed = c.value("bed", "");
    sec.crop = c.value("crop", "wheat");
    sec.x = c["x"];
    sec.y = c["y"];
    sec.length = c.value("length", 10.0);
    sec.width = c.value("width", 4.0);
    sec.angle = c.value("angle", 0.0);
    sec.crop_height = c.value("crop_height", 0.8);
    sec.moisture = c.value("moisture", 0.5);
    sec.state = stateFrom(c.value("state", "fallow"));
    Rgb col = cropColour(sec.crop, sec.state);
    double hgt = stateHeight(sec.crop_height, sec.state);
    // a 0.3 m gap between consecutive sections makes the management units readable
    beds << " Solid { translation " << f(sec.x) << " " << f(sec.y) << " " << f(hgt / 2) << " rotation 0 0 1 " << f(sec.angle)
         << " name \"bed_" << sec.id << "\" children [ Shape { appearance PBRAppearance { baseColor " << f(col.r) << " " << f(col.g)
         << " " << f(col.b) << " roughness 1 metalness 0 } geometry Box { size " << f(std::max(0.5, sec.length - 0.3)) << " "
         << f(sec.width) << " " << f(hgt) << " } } ] }";
    weeds << " Solid { translation " << f(sec.x) << " " << f(sec.y) << " -2 name \"weed_" << sec.id
          << "\" children [ Shape { appearance PBRAppearance { baseColor 0.95 0.2 0.25 roughness 0.8 emissiveColor 0.4 0 0 } geometry Sphere { radius "
          << f(std::max(0.5, sec.width * 0.12)) << " subdivision 1 } } ] }";
    order.push_back(sec.id);
    world.sections[sec.id] = sec;
  }
  for (auto *os : {&tracks, &water, &beds, &weeds}) *os << " ] }";
  ch->importMFNodeFromString(-1, tracks.str());
  ch->importMFNodeFromString(-1, water.str());
  ch->importMFNodeFromString(-1, beds.str());
  webots::Field *bed_children = ch->getMFNode(ch->getCount() - 1)->getField("children");
  ch->importMFNodeFromString(-1, weeds.str());
  webots::Field *weed_children = ch->getMFNode(ch->getCount() - 1)->getField("children");
  for (size_t i = 0; i < order.size(); ++i) {
    Section &sec = world.sections[order[i]];
    sec.node = bed_children->getMFNode(int(i));
    sec.weed_node = weed_children->getMFNode(int(i));
  }
}

void WorldSpawner::setCropState(Section &c) {
  if (!c.node) return;
  Rgb col = cropColour(c.crop, c.state);
  double hgt = stateHeight(c.crop_height, c.state);
  webots::Node *shape = c.node->getField("children")->getMFNode(0);
  if (shape) {
    if (webots::Node *geo = shape->getField("geometry")->getSFNode()) {
      const double size[3] = {std::max(0.5, c.length - 0.3), c.width, hgt};
      geo->getField("size")->setSFVec3f(size);
    }
    if (webots::Node *app = shape->getField("appearance")->getSFNode()) {
      const double rgb[3] = {col.r, col.g, col.b};
      app->getField("baseColor")->setSFColor(rgb);
    }
  }
  const double tr[3] = {c.x, c.y, hgt / 2};
  c.node->getField("translation")->setSFVec3f(tr);
  if (c.weeds) setWeeds(c, true);  // keep the weed marker above the (re)grown crop
}

void WorldSpawner::setWeeds(Section &c, bool on) {
  c.weeds = on;
  if (!c.weed_node) return;
  const double top = stateHeight(c.crop_height, c.state);
  const double tr[3] = {c.x, c.y, on ? top + 0.6 : -2.0};
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
