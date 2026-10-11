// Original offline-only SDK adapter. Timing remains UNQUALIFIED until native
// tests. Build with MSVC x64 + externally supplied BakkesMod SDK; see
// NATIVE_INSTRUMENT.md.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "bakkesmod/plugin/bakkesmodplugin.h"
#include "bakkesmod/wrappers/Engine/EngineTAWrapper.h"
#include "bakkesmod/wrappers/Engine/WorldInfoWrapper.h"
#include "bakkesmod/wrappers/GameEvent/ServerWrapper.h"
#include "bakkesmod/wrappers/GameEvent/GameEventWrapper.h"
#include "bakkesmod/wrappers/GameObject/BoostPickupWrapper.h"
#include "bakkesmod/wrappers/GameObject/CameraWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/BoostWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/DodgeComponentWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/DoubleJumpComponentWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/FlipCarComponentWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/JumpComponentWrapper.h"
#include "bakkesmod/wrappers/GameObject/CarComponent/VehicleSimWrapper.h"
#include "bakkesmod/wrappers/GameObject/PriWrapper.h"
#include "bakkesmod/wrappers/SettingsWrapper.h"
#include "bakkesmod/wrappers/arraywrapper.h"
#include "core.hpp"
#include <filesystem>
#include <iomanip>
#include <locale>
#include <set>
#include <sstream>
#include <typeindex>
#include <windows.h>
using namespace noodle::instrument;
class RocketLaboratoryInstrument final : public BakkesMod::Plugin::BakkesModPlugin {
  std::unique_ptr<Writer> writer;
  Controller controller{120};
  FixedPreparation fixed_preparation;
  uintptr_t preparation_car_handle = 0;
  uint64_t preparation_generation = 0, preparation_queued_ns = 0;
  int64_t preparation_queued_frame = -1;
  std::string preparation_map;
  IdentityRegistry identities;
  std::set<uintptr_t> known_car_handles;
  std::set<uint32_t> discovered_pads;
  std::set<uint32_t> tape_actors;
  std::map<uint32_t, std::array<float, 4>> preparation_suspension;
  uint64_t capture_start = 0, callback_sequence = 0;
  int64_t last_frame = -1;
  bool neutralizing = false;
  bool experiment_failed = false;
  CommandInbox inbox;
  std::string bridge_session;
  uint64_t bridge_expires = 0;
  uint64_t generation = 0;
  std::filesystem::path capture_path;
  std::unique_ptr<ServerWrapper> diagnostic_server;
  uint64_t diagnostic_server_observed_ns = 0;
  bool diagnostic_server_authority = false;
  std::string diagnostic_server_hook;
  std::shared_ptr<std::atomic<bool>> alive =
      std::make_shared<std::atomic<bool>>(true);
  static constexpr const char *input_hook =
      "Function TAGame.Car_TA.SetVehicleInput";
  static constexpr const char *camera_hook =
      "Function Engine.GameViewportClient.Tick";
  static constexpr const char *server_time_hook =
      "Function TAGame.GameEvent_Soccar_TA.OnGameTimeUpdated";
  static constexpr const char *server_countdown_hook =
      "Function GameEvent_Soccar_TA.Countdown.BeginState";
  bool offline() {
    return !gameWrapper->IsInOnlineGame() && !gameWrapper->IsInReplay() &&
           (gameWrapper->IsInGame() || gameWrapper->IsInFreeplay() ||
            gameWrapper->IsInCustomTraining()) &&
           !gameWrapper->GetGameEventAsServer().IsNull();
  }
  uint32_t id(uintptr_t address) {
    auto existing = identities.lookup(address);
    return existing ? existing : identities.observe(address, "other-actor");
  }
  std::string player_key(CarWrapper car) {
    auto pri = car.GetPRI();
    if (pri.IsNull())
      return {};
    auto unique = pri.GetUniqueIdWrapper();
    return unique.GetIdString() +
           "|match-player:" + std::to_string(pri.GetPlayerID());
  }
  uint32_t car_id(CarWrapper car) {
    if (car.IsNull())
      return 0;
    return identities.observe(car.memory_address, "car:" + player_key(car));
  }
  static std::string json_string(const std::string &value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
      if (c == '"' || c == '\\')
        out << '\\' << char(c);
      else if (c < 32) {
        out << "\\u00" << std::hex << std::setw(2) << std::setfill('0')
            << unsigned(c) << std::dec;
      } else
        out << char(c);
    }
    out << '"';
    return out.str();
  }
  void pad_sample(ActorWrapper caller, uint32_t event_code) {
    if (!writer || !offline() || caller.IsNull())
      return;
    BoostPickupWrapper pad(caller.memory_address);
    if (pad.IsNull())
      return;
    auto actor = id(pad.memory_address);
    discovered_pads.insert(actor);
    Fields f;
    vector(f, 1, pad.GetLocation());
    f.f(140, pad.GetBoostAmount());
    f.u(141, pad.GetBoostType());
    f.u(142, pad.GetbPickedUp());
    f.u(143, pad.GetbNoPickup());
    f.f(144, pad.GetRespawnDelay());
    f.u(145, event_code);
    auto engine = gameWrapper->GetEngine();
    if (!engine.IsNull())
      f.f(146, engine.GetPhysicsTime());
    f.u(147, 0); // No native remaining-timer getter exposed.
    emit(Kind::Pad, 2, actor, std::move(f));
  }
  int64_t frame() {
    auto engine = gameWrapper->GetEngine();
    return engine.IsNull() ? -1 : engine.GetPhysicsFrame();
  }
  static Controls controls(ControllerInput in) {
    Controls c;
    c.axes = {in.Throttle, in.Steer,        in.Pitch,      in.Yaw,
              in.Roll,     in.DodgeForward, in.DodgeStrafe};
    c.buttons = {bool(in.Handbrake), bool(in.Jump), bool(in.ActivateBoost),
                 bool(in.HoldingBoost), bool(in.Jumped)};
    return c;
  }
  static ControllerInput native(Controls c) {
    ControllerInput in{};
    in.Throttle = c.axes[0];
    in.Steer = c.axes[1];
    in.Pitch = c.axes[2];
    in.Yaw = c.axes[3];
    in.Roll = c.axes[4];
    in.DodgeForward = c.axes[5];
    in.DodgeStrafe = c.axes[6];
    in.Handbrake = c.buttons[0];
    in.Jump = c.buttons[1];
    in.ActivateBoost = c.buttons[2];
    in.HoldingBoost = c.buttons[3];
    in.Jumped = c.buttons[4];
    return in;
  }
  void emit(Kind kind, uint16_t phase, uint32_t actor, Fields f) {
    if (writer &&
        !writer->submit({kind, phase, now_ns(), frame(), actor, std::move(f)}))
      controller.stop();
  }
  static void vector(Fields &f, uint16_t key, Vector v) {
    f.vec(key, v.X, v.Y, v.Z);
  }
  static void rotation(Fields &f, uint16_t key, Rotator r) {
    f.rot(key, r.Pitch, r.Yaw, r.Roll);
  }
  static void camera_target_rb_fields(Fields &f, uint16_t first, RBState state) {
    f.quat(first, state.Quaternion.W, state.Quaternion.X, state.Quaternion.Y,
           state.Quaternion.Z);
    f.f(uint16_t(first + 1), state.Time);
    vector(f, uint16_t(first + 2), state.Location);
    vector(f, uint16_t(first + 3), state.LinearVelocity);
    vector(f, uint16_t(first + 4), state.AngularVelocity);
    f.u(uint16_t(first + 5), uint32_t(state.bSleeping) |
                               (uint32_t(state.bNewData) << 1));
  }
  static void input_fields(Fields &f, uint16_t first, Controls c) {
    for (unsigned i = 0; i < 7; ++i)
      f.f(uint16_t(first + i), c.axes[i]);
    uint32_t bits = 0;
    for (unsigned i = 0; i < 5; ++i)
      bits |= uint32_t(c.buttons[i]) << i;
    f.u(uint16_t(first + 7), bits);
  }
  void input_record(Kind kind, uint16_t phase, CarWrapper car, Controls c,
                    const ControllerInput *post_parameter = nullptr) {
    Fields f;
    input_fields(f, 100, c);
    if (kind == Kind::InputConsumed) {
      // Public named getters and the documented callback parameter are distinct
      // observations. Do not assume any is the solver's authoritative input.
      f.u(180, post_parameter != nullptr);
      if (post_parameter)
        input_fields(f, 160, controls(*post_parameter));
      input_fields(f, 170, controls(car.VehicleWrapper::GetInput()));
      f.u(178, car.GetReplicatedThrottle());
      f.u(179, car.GetReplicatedSteer());
    }
    f.u(84, uint32_t(callback_sequence));
    emit(kind, phase, car_id(car), std::move(f));
  }
  template <class T> Fields body_fields(T object) {
    Fields f;
    vector(f, 1, object.GetLocation());
    rotation(f, 2, object.GetRotation());
    vector(f, 3, object.GetVelocity());
    vector(f, 4, object.GetAngularVelocity());
    f.f(5, object.GetPhysicsTime());
    // Stored and queried-current public RB states remain distinct so native
    // qualification can detect staleness/phase differences. Never derive the
    // quaternion from the lower precision integer Rotator above.
    auto stored = object.GetRBState();
    f.quat(6, stored.Quaternion.W, stored.Quaternion.X, stored.Quaternion.Y,
           stored.Quaternion.Z);
    f.f(9, stored.Time);
    vector(f, 10, stored.Location);
    vector(f, 11, stored.LinearVelocity);
    vector(f, 12, stored.AngularVelocity);
    f.u(19, uint32_t(stored.bSleeping) | (uint32_t(stored.bNewData) << 1));
    auto current = object.GetCurrentRBState();
    f.quat(13, current.Quaternion.W, current.Quaternion.X, current.Quaternion.Y,
           current.Quaternion.Z);
    f.f(14, current.Time);
    vector(f, 15, current.Location);
    vector(f, 16, current.LinearVelocity);
    vector(f, 17, current.AngularVelocity);
    f.u(18, uint32_t(current.bSleeping) | (uint32_t(current.bNewData) << 1));
    auto old = object.GetOldRBState();
    f.quat(150, old.Quaternion.W, old.Quaternion.X, old.Quaternion.Y,
           old.Quaternion.Z);
    f.f(151, old.Time);
    vector(f, 152, old.Location);
    vector(f, 153, old.LinearVelocity);
    vector(f, 154, old.AngularVelocity);
    f.u(155, uint32_t(old.bSleeping) | (uint32_t(old.bNewData) << 1));
    auto engine = gameWrapper->GetEngine();
    if (!engine.IsNull())
      f.f(156, engine.GetRenderAlpha());
    f.u(84, uint32_t(callback_sequence));
    return f;
  }
  template <class T> void body(T object, uint32_t actor) {
    emit(Kind::Body, 2, actor, body_fields(object));
  }
  void body(CarWrapper car, uint32_t actor) {
    auto f = body_fields(car);
    // Public getters, sequentially after the body tuple. No interpretation of
    // native ID/family, half/full extent, actual contact solver or mesh bounds.
    const int32_t native_body_id = car.GetLoadoutBody();
    auto extent = car.VehicleWrapper::GetLocalCollisionExtent();
    auto offset = car.VehicleWrapper::GetLocalCollisionOffset();
    BodyConfiguration{native_body_id, {extent.X, extent.Y, extent.Z},
                       {offset.X, offset.Y, offset.Z}}
        .append(f, actor, uint32_t(generation));
    auto jump = car.GetJumpComponent();
    JumpConfiguration jump_config;
    if (!jump.IsNull()) {
      jump_config.component_present = true;
      jump_config.values = {jump.GetMinJumpTime(), jump.GetJumpImpulse(),
          jump.GetJumpForce(), jump.GetJumpForceTime(), jump.GetPodiumJumpForceTime(),
          jump.GetJumpImpulseSpeed(), jump.GetJumpAccel(), jump.GetMaxJumpHeight(),
          jump.GetMaxJumpHeightTime()};
    }
    jump_config.append(f, actor, uint32_t(generation));
    emit(Kind::Body, 2, actor, std::move(f));
  }
  FixedPreparationGate fixed_gate(CarWrapper car, const Controls &input) {
    FixedPreparationGate gate;
    gate.offline = offline();
    gate.capture_active = bool(writer);
    gate.capture_valid = writer && writer->valid();
    gate.controller_stage = controller.stage();
    gate.tape_empty = tape_actors.empty();
    gate.neutral = authored_neutral(input);
    gate.map_matches = gameWrapper->GetCurrentMap() == "Stadium_P" &&
                       (preparation_map.empty() ||
                        preparation_map == gameWrapper->GetCurrentMap());
    gate.generation_matches = !preparation_car_handle ||
                              preparation_generation == generation;
    if (!gate.offline || car.IsNull()) return gate;
    auto local = gameWrapper->GetLocalCar();
    auto cars = gameWrapper->GetGameEventAsServer().GetCars();
    gate.single_local_actor = !local.IsNull() && cars.Count() == 1 &&
                             !cars.Get(0).IsNull() &&
                             cars.Get(0).memory_address == car.memory_address &&
                             local.memory_address == car.memory_address;
    gate.identity_matches = car_id(car) == 1 &&
                            (!preparation_car_handle ||
                             preparation_car_handle == car.memory_address);
    return gate;
  }
  void fixed_failure() {
    fixed_preparation.fail();
    experiment_failed = true;
    if (!writer) return;
    Fields f;
    f.u(60, 13); f.u(62, uint32_t(generation));
    f.u(83, uint32_t(controller.stage()));
    f.u(84, uint32_t(callback_sequence));
    emit(Kind::Event, 1, 1, std::move(f));
  }
  void queue_fixed() {
    auto car = gameWrapper->GetLocalCar();
    if (car.IsNull()) throw std::runtime_error("fixed preparation needs local car");
    fixed_preparation.queue(now_ns(), fixed_gate(car, controls(car.GetInput())));
    preparation_car_handle = car.memory_address;
    preparation_generation = generation;
    preparation_map = gameWrapper->GetCurrentMap();
    preparation_queued_ns = now_ns();
    preparation_queued_frame = frame();
  }
  void apply_fixed(CarWrapper car, ControllerInput &input) {
    if (fixed_preparation.state() != FixedPreparationState::Queued) return;
    if (!fixed_preparation.begin(now_ns(), fixed_gate(car, controls(input)),
                                 frame(), callback_sequence)) {
      if (fixed_preparation.state() == FixedPreparationState::Failed)
        fixed_failure();
      return;
    }
    try {
      // Only this fixed numeric recipe exists. No contact/jump/suspension state,
      // old/current RB fields, clock, boost or ball is assigned here. These
      // public void setter calls returning is not a native solver acknowledgement.
      car.SetLocation(Vector(0.f, -3000.f, 200.f));
      car.SetRotation(Rotator{0, 0, 0});
      car.SetVelocity(Vector(0.f, 0.f, 0.f));
      car.SetAngularVelocity(Vector(0.f, 0.f, 0.f), false);
      fixed_preparation.invoked();
      Fields applied;
      applied.u(60, 11); applied.u(62, uint32_t(generation));
      applied.u(83, uint32_t(controller.stage()));
      applied.u(84, uint32_t(callback_sequence));
      vector(applied, 1, Vector(0.f, -3000.f, 200.f));
      rotation(applied, 2, Rotator{0, 0, 0});
      vector(applied, 3, Vector(0.f, 0.f, 0.f));
      vector(applied, 4, Vector(0.f, 0.f, 0.f));
      emit(Kind::Event, 1, 1, std::move(applied));
      // Separate immediate sequential getters; they may legitimately expose
      // different phases/stale stored data. Later BODY samples establish fall.
      auto readback = body_fields(car);
      readback.u(60, 12); readback.u(62, uint32_t(generation));
      readback.u(83, uint32_t(controller.stage()));
      emit(Kind::Event, 1, 1, std::move(readback));
      if (!writer || !writer->valid()) fixed_failure();
      preparation_suspension.clear();
    } catch (const std::exception &e) {
      cvarManager->log(e.what());
      fixed_failure(); // Consumed before calls: never retry after partial writes.
    }
  }
  template <class T> void component(T comp, uint32_t actor, uint32_t type) {
    if (comp.IsNull())
      return;
    Fields f;
    f.u(45, type);
    f.u(40, comp.GetbActive());
    f.f(41, comp.GetActivityTime());
    f.f(42, comp.GetActiveTime());
    f.f(43, comp.GetInactiveTime());
    emit(Kind::Mechanic, 2, actor, std::move(f));
  }
  bool settled(CarWrapper car) {
    if (car.IsNull() || car.GetNumWheelWorldContacts() != 4 ||
        !car.GetbCanJump() || car.GetbJumped() || car.GetbDoubleJumped() ||
        car.IsDodging() || car.GetGroundNormal().Z < .99f ||
        car.GetInput().Jump)
      return false;
    auto v = car.GetVelocity();
    auto w = car.GetAngularVelocity();
    if (v.magnitude() >= 1.f || w.magnitude() >= .01f)
      return false;
    auto jump = car.GetJumpComponent();
    auto twice = car.GetDoubleJumpComponent();
    auto dodge = car.GetDodgeComponent();
    auto flip = car.GetFlipComponent();
    if (jump.IsNull() || twice.IsNull() || dodge.IsNull() || flip.IsNull() ||
        jump.GetbActive() || twice.GetbActive() || dodge.GetbActive() ||
        flip.GetbActive())
      return false;
    auto sim = car.GetVehicleSim();
    if (sim.IsNull())
      return false;
    auto wheels = sim.GetWheels();
    if (wheels.Count() != 4)
      return false;
    std::array<float, 4> current;
    for (int i = 0; i < 4; ++i) {
      auto wheel = wheels.Get(i);
      if (wheel.IsNull())
        return false;
      current[i] = wheel.GetSuspensionDistance();
      if (!std::isfinite(current[i]))
        return false;
    }
    auto actor = car_id(car);
    auto previous = preparation_suspension.find(actor);
    bool quiet = previous != preparation_suspension.end();
    if (quiet)
      for (int i = 0; i < 4; ++i)
        quiet &= std::abs(current[i] - previous->second[i]) <= .05f;
    preparation_suspension[actor] = current;
    return quiet;
  }
  void world_sample() {
    auto server = gameWrapper->GetGameEventAsServer();
    auto cars = server.GetCars();
    std::set<uint32_t> present;
    std::set<uintptr_t> seen_handles;
    bool ready = true;
    if (cars.Count() > 64) {
      controller.stop();
      return;
    }
    for (int i = 0; i < cars.Count(); ++i) {
      auto car = cars.Get(i);
      if (car.IsNull())
        continue;
      auto actor = car_id(car);
      present.insert(actor);
      seen_handles.insert(car.memory_address);
      body(car, actor);
      if (tape_actors.count(actor) && !settled(car))
        ready = false;
      Fields f;
      f.u(7, car.GetTeamNum2());
      f.u(130, identities.player(player_key(car)));
      auto pri = car.GetPRI();
      if (!pri.IsNull()) {
        f.i(131, pri.GetPlayerID());
        f.u(132, pri.GetbBot());
      }
      auto boost = car.GetBoostComponent();
      if (!boost.IsNull())
        f.f(8, boost.GetCurrentBoostAmount());
      f.u(20, uint32_t(car.GetbCanJump()) | (uint32_t(car.GetbJumped()) << 1) |
                  (uint32_t(car.GetbDoubleJumped()) << 2) |
                  (uint32_t(car.GetbOnGround()) << 3) |
                  (uint32_t(car.GetbSuperSonic()) << 4) |
                  (uint32_t(car.IsDodging()) << 5));
      f.u(21, car.GetNumWheelWorldContacts());
      f.u(22, car.GetNumWheelContacts());
      f.i(23, car.GetVehicleUpdateTag());
      vector(f, 24, car.GetGroundNormal());
      f.f(25, car.GetTimeOnGround());
      f.f(26, car.GetTimeOffGround());
      auto contact = car.GetWorldContact();
      f.u(27, contact.bHasContact);
      vector(f, 28, contact.Normal);
      vector(f, 29, contact.Location);
      auto sticky = car.GetStickyForce();
      f.f(95, sticky.Ground);
      f.f(96, sticky.Wall);
      emit(Kind::Mechanic, 2, actor, std::move(f));
      auto sim = car.GetVehicleSim();
      if (!sim.IsNull()) {
        auto wheels = sim.GetWheels();
        if (wheels.Count() > 16) {
          controller.stop();
          return;
        }
        for (int j = 0; j < wheels.Count(); ++j) {
          auto wheel = wheels.Get(j);
          if (wheel.IsNull())
            continue;
          auto contact = wheel.GetContact();
          Fields f;
          f.u(39, wheel.GetWheelIndex());
          f.u(30, uint32_t(contact.bHasContact) |
                      (uint32_t(contact.bHasContactWithWorldGeometry) << 1) |
                      (uint32_t(wheel.GetbHadContact()) << 2));
          vector(f, 31, contact.Location);
          vector(f, 32, contact.Normal);
          vector(f, 33, wheel.GetLinearVelocity());
          f.f(34, wheel.GetSuspensionDistance());
          f.f(35, wheel.GetSpinSpeed());
          f.f(36, wheel.GetSteer2());
          f.f(37, contact.HasContactChangeTime);
          f.f(38, wheel.GetFrictionCurveInput());
          f.u(61, id(reinterpret_cast<uintptr_t>(contact.Actor)));
          f.f(90, wheel.GetSuspensionStiffness());
          f.f(91, wheel.GetSuspensionDampingCompression());
          f.f(92, wheel.GetSuspensionDampingRelaxation());
          f.f(93, wheel.GetSuspensionTravel());
          f.f(94, wheel.GetWheelRadius());
          emit(Kind::Wheel, 2, actor, std::move(f));
        }
      }
      component(car.GetJumpComponent(), actor, 1);
      component(car.GetDoubleJumpComponent(), actor, 2);
      auto dodge = car.GetDodgeComponent();
      component(dodge, actor, 3);
      if (!dodge.IsNull()) {
        Fields f;
        f.u(45, 3);
        vector(f, 44, dodge.GetDodgeDirection());
        emit(Kind::Mechanic, 2, actor, std::move(f));
      }
      component(car.GetFlipComponent(), actor, 4);
    }
    for (auto actor : tape_actors)
      if (!present.count(actor)) {
        ready = false;
        experiment_failed = true;
        controller.stop();
        neutral();
        tape_actors.clear();
        break;
      }
    for (auto handle : known_car_handles)
      if (!seen_handles.count(handle))
        identities.retire(handle);
    known_car_handles = std::move(seen_handles);
    auto ball = server.GetBall();
    if (!ball.IsNull())
      body(ball, id(ball.memory_address));
    Fields world;
    world.u(85, uint32_t(present.size()));
    world.u(148, uint32_t(discovered_pads.size()));
    world.u(149, 0); // Partial event discovery, not full map inventory.
    auto engine = gameWrapper->GetEngine();
    if (!engine.IsNull()) {
      world.f(71, engine.GetPhysicsFramerate());
      world.f(74, engine.GetBulletFixedDeltaTime());
      world.f(156, engine.GetRenderAlpha());
    }
    auto info = server.GetWorldInfo();
    if (!info.IsNull()) {
      world.f(70, info.GetWorldGravityZ());
      world.f(72, info.GetTimeDilation());
      world.f(73, info.GetRBPhysicsGravityScaling());
    }
    world.u(62, uint32_t(generation));
    emit(Kind::World, 2, 0, std::move(world));
    if (!tape_actors.empty()) {
      auto previous_stage = controller.stage();
      controller.advance(frame(), ready, now_ns(), offline());
      if (controller.stage() != previous_stage) {
        Fields stage;
        stage.u(60, 10);
        stage.u(83, uint32_t(controller.stage()));
        emit(Kind::Event, 2, 0, std::move(stage));
      }
      if (controller.stage() == Stage::Failed)
        experiment_failed = true;
      if (controller.stage() == Stage::Done ||
          controller.stage() == Stage::Failed) {
        neutral();
        tape_actors.clear();
      }
    }
  }
  void neutral() {
    if (neutralizing || !offline())
      return;
    neutralizing = true;
    auto cars = gameWrapper->GetGameEventAsServer().GetCars();
    for (int i = 0; i < cars.Count(); ++i) {
      auto car = cars.Get(i);
      if (!car.IsNull() && tape_actors.count(car_id(car)))
        car.SetInput(ControllerInput{});
    }
    neutralizing = false;
  }
  void stop() {
    fixed_preparation.cancel();
    auto terminal_stage = controller.stage();
    if (terminal_stage == Stage::Failed)
      experiment_failed = true;
    controller.stop();
    neutral();
    tape_actors.clear();
    if (!writer)
      return;
    Fields f;
    f.u(80, uint32_t(writer->drop_count()));
    f.u(81, writer->valid() ? 1 : 0);
    f.u(82, 0);
    f.u(83, uint32_t(terminal_stage));
    f.u(86, experiment_failed ? 1 : 0);
    emit(Kind::Quality, 4, 0, std::move(f));
    writer->close();
    std::ofstream status(capture_path.string() + ".status.json");
    status << "{\"schema\":\"noodle.instrument.status.v1\",\"writer_valid\":"
           << (writer->valid() ? "true" : "false")
           << ",\"dropped_records\":" << writer->drop_count()
           << ",\"timing_qualified\":false,\"native_snapshot_restore_"
              "qualified\":false}";
    writer.reset();
  }
  void watchdog() {
    if (!writer)
      return;
    if (!offline() || !writer->valid() ||
        now_ns() - capture_start > 120000000000ull) {
      stop();
      return;
    }
    if (fixed_preparation.state() == FixedPreparationState::Queued) {
      auto car = gameWrapper->GetLocalCar();
      auto input = car.IsNull() ? Controls{} : controls(car.GetInput());
      if (!fixed_preparation.check(now_ns(), fixed_gate(car, input)))
        fixed_failure();
    }
    if (!tape_actors.empty())
      controller.get(*tape_actors.begin(), frame(), now_ns(), offline());
    if (controller.stage() == Stage::Failed) {
      experiment_failed = true;
      neutral();
      tape_actors.clear();
    }
    auto token = std::weak_ptr<std::atomic<bool>>(alive);
    auto epoch = capture_start;
    gameWrapper->SetTimeout(
        [this, token, epoch](GameWrapper *) {
          auto guard = token.lock();
          if (guard && guard->load() && capture_start == epoch)
            watchdog();
        },
        .25f);
  }
  void start() {
    stop();
    if (!offline())
      throw std::runtime_error("offline local game required");
    auto dir = std::filesystem::path(gameWrapper->GetDataFolderW()) /
               "noodle-instrument";
    std::filesystem::create_directories(dir);
    capture_path = dir / (std::to_string(now_ns()) + ".nni");
    writer = std::make_unique<Writer>(capture_path.string());
    capture_start = now_ns();
    experiment_failed = false;
    last_frame = -1;
    callback_sequence = 0;
    identities.reset();
    fixed_preparation.reset();
    preparation_car_handle = 0;
    preparation_generation = preparation_queued_ns = 0;
    preparation_queued_frame = -1;
    preparation_map.clear();
    known_car_handles.clear();
    discovered_pads.clear();
    ++generation;
    std::ofstream manifest(capture_path.string() + ".manifest.json");
    manifest.imbue(std::locale::classic());
    manifest << std::setprecision(9);
    auto settings = gameWrapper->GetSettings();
    auto cam = settings.GetCameraSettings();
    auto pad = settings.GetGamepadSettings();
    auto camera_save = settings.GetCameraSaveSettings();
    auto video = settings.GetVideoSettings();
    manifest
        << "{\"schema\":\"noodle.instrument.manifest.v1\",\"stream\":\"NNI2\","
           "\"sdk_commit\":\"479e8f571cf554b25f4eeb64d611dec4133edcaf\","
           "\"timing_qualified\":false,\"stage_at_start\":0,\"body_phase\":"
           "\"first post-SetVehicleInput callback for engine "
           "frame\",\"camera_phase\":\"post-GameViewportClient.Tick\","
           "\"units\":\"native uu, seconds, radians/s, integer Unreal "
           "rotator\",\"max_bytes\":134217728,\"max_wall_seconds\":120,";
    manifest << "\"input_delivery_method\":\"direct documented pre-hook parameter field mutation\","
             << "\"controller_input_size_bytes\":" << sizeof(ControllerInput)
             << ",\"body_orientation_sources\":[\"Actor.GetRotation integer Rotator\","
                "\"RBActor.GetRBState Quaternion WXYZ\",\"RBActor.GetCurrentRBState Quaternion WXYZ\","
                "\"RBActor.GetOldRBState Quaternion WXYZ\"],"
                "\"render_alpha_source\":\"Engine.GetRenderAlpha at body and world readback; interpolation meaning unqualified\",";
    manifest << "\"native_body_configuration_observation\":{"
                "\"record\":\"car-only BODY TLVs250..257, header actor/frame/phase and callback sequence84\","
                "\"phase\":\"sequential after body getters in first post-SetVehicleInput sample\","
                "\"sources\":[\"CarWrapper.GetLoadoutBody\",\"VehicleWrapper.GetLocalCollisionExtent\","
                "\"VehicleWrapper.GetLocalCollisionOffset\"],"
                "\"source_mask_bits\":{\"0\":\"positive native body ID\",\"1\":\"finite positive extent XYZ\","
                "\"2\":\"finite offset XYZ\"},\"units\":\"native local XYZ uu; no half/full extent interpretation\","
                "\"missing_values\":\"omitted, complete presence false; no defaults\","
                "\"native_runtime_qualified\":false,\"collision_shape_qualified\":false,\"timing_qualified\":false},";
    manifest << "\"native_jump_configuration_observation\":{"
                "\"record\":\"car-only BODY TLVs260..265/270..278, header actor/frame/phase and callback sequence84\","
                "\"phase\":\"sequential after body configuration getters in first post-SetVehicleInput sample\","
                "\"component_source\":\"VehicleWrapper.GetJumpComponent\","
                "\"sources\":[\"JumpComponentWrapper.GetMinJumpTime\",\"JumpComponentWrapper.GetJumpImpulse\","
                "\"JumpComponentWrapper.GetJumpForce\",\"JumpComponentWrapper.GetJumpForceTime\","
                "\"JumpComponentWrapper.GetPodiumJumpForceTime\",\"JumpComponentWrapper.GetJumpImpulseSpeed\","
                "\"JumpComponentWrapper.GetJumpAccel\",\"JumpComponentWrapper.GetMaxJumpHeight\","
                "\"JumpComponentWrapper.GetMaxJumpHeightTime\"],"
                "\"mask_order\":\"source array order bits0..8; all511\","
                "\"units\":\"raw getter floats; unit and effective force semantics unqualified\","
                "\"missing_values\":\"null component attempted0 valid0; present attempted511, invalid omitted; no defaults\","
                "\"native_runtime_qualified\":false,\"effective_force_law_qualified\":false,\"timing_qualified\":false},";
    manifest << "\"native_fixed_preparation\":{\"recipe_id\":\"native_actor_fixed_pose_v1\","
                "\"application_phase\":\"existing pre-SetVehicleInput callback, before tape arm\","
                "\"event_codes\":{\"11\":\"four public Actor setter calls returned; requested values\","
                "\"12\":\"separate immediate Actor/stored/current/old RB getters\",\"13\":\"preparation failure\"},"
                "\"runtime_setter_semantics_qualified\":false,\"hidden_state_restored\":false,"
                "\"timing_qualified\":false},";
    manifest << "\"input_readback_sources\":[\"CarWrapper.GetInput\","
                "\"post-hook ControllerInput parameter\",\"explicit VehicleWrapper.GetInput\","
                "\"VehicleWrapper.GetReplicatedThrottle/Steer raw bytes\"],";
    manifest << "\"camera_target_observation\":{"
                "\"scope\":\"GetLocalCar observation; not proof of actual native camera target\","
                "\"phase\":\"same post-GameViewportClient.Tick callback as POV; sequential getter readbacks, not atomic\","
                "\"quaternion_convention\":\"native float32 W,X,Y,Z; no basis conversion\","
                "\"sources\":[\"Actor.GetLocation/GetVelocity/GetAngularVelocity/GetRotation\","
                "\"RBActor.GetRBState/GetCurrentRBState/GetOldRBState\",\"RBActor.GetPhysicsTime\","
                "\"Engine.GetRenderAlpha\"],\"timing_qualified\":false},";
    manifest << "\"reference\":{\"sdk_steam_version\":"
             << gameWrapper->GetSteamVersion() << ",\"psy_build_id\":"
             << json_string(gameWrapper->GetPsyBuildID())
             << ",\"bakkesmod_version\":" << gameWrapper->GetBakkesModVersion()
             << ",\"map\":" << json_string(gameWrapper->GetCurrentMap())
             << ",\"steam_appmanifest_build_id\":null},";
    manifest << "\"camera_settings\":{\"fov\":" << cam.FOV
             << ",\"height\":" << cam.Height << ",\"pitch\":" << cam.Pitch
             << ",\"distance\":" << cam.Distance
             << ",\"stiffness\":" << cam.Stiffness
             << ",\"swivel_speed\":" << cam.SwivelSpeed
             << ",\"transition_speed\":" << cam.TransitionSpeed
             << ",\"shake\":" << (camera_save.CameraShake ? "true" : "false")
             << ",\"invert_swivel_pitch\":"
             << (camera_save.InvertSwivelPitch ? "true" : "false") << "},";
    manifest << "\"control_settings\":{\"controller_deadzone\":"
             << pad.ControllerDeadzone
             << ",\"dodge_input_threshold\":" << pad.DodgeInputThreshold
             << ",\"steering_sensitivity\":" << pad.SteeringSensitivity
             << ",\"air_control_sensitivity\":" << pad.AirControlSensitivity
             << "},";
    manifest << "\"video_settings\":{\"vsync\":"
             << (video.bVsync ? "true" : "false") << ",\"uncapped_framerate\":"
             << (video.bUncappedFramerate ? "true" : "false")
             << "},\"gamepad_bindings\":[";
    bool first = true;
    for (auto &binding : settings.GetAllGamepadBindings()) {
      if (!first)
        manifest << ',';
      first = false;
      manifest << '[' << json_string(binding.first) << ','
               << json_string(binding.second) << ']';
    }
    manifest << "],\"pc_bindings\":[";
    first = true;
    for (auto &binding : settings.GetAllPCBindings()) {
      if (!first)
        manifest << ',';
      first = false;
      manifest << '[' << json_string(binding.first) << ','
               << json_string(binding.second) << ']';
    }
    manifest << "],\"pad_inventory_complete\":false,\"native_pad_remaining_"
                "timer_available\":false,\"actor_lifecycle_qualified\":false}";
    watchdog();
  }
  std::filesystem::path command_dir() {
    return std::filesystem::path(gameWrapper->GetDataFolderW()) /
           "noodle-instrument";
  }
  static std::string small_file(const std::filesystem::path &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file)
      throw std::runtime_error("cannot open control file");
    std::array<char, 1025> buffer{};
    file.read(buffer.data(), buffer.size());
    auto count = file.gcount();
    if (count > 1024)
      throw std::runtime_error("control file exceeds 1 KiB");
    return std::string(buffer.data(), size_t(count));
  }
  std::string bridge_status() {
    std::ostringstream out;
    out << "\"capture_active\":" << (writer ? "true" : "false")
        << ",\"controller_stage\":" << uint32_t(controller.stage())
        << ",\"native_preparation\":{\"recipe_id\":"
        << json_string(FixedPreparation::recipe) << ",\"state\":"
        << json_string(fixed_preparation.name())
        << ",\"actor\":1,\"queued_engine_frame\":" << preparation_queued_frame
        << ",\"queued_monotonic_ns\":" << preparation_queued_ns
        << ",\"apply_engine_frame\":" << fixed_preparation.applied_frame()
        << ",\"apply_callback_sequence\":" << fixed_preparation.applied_sequence()
        << ",\"registry_generation\":" << generation
        << ",\"timing_qualified\":false,\"hidden_state_restored\":false}"
        << ",\"timing_qualified\":false,\"capture_file\":"
        << json_string(capture_path.filename().string())
        << ",\"game_context\":" << game_context() << ",\"actors\":[";
    if (offline()) {
      auto cars = gameWrapper->GetGameEventAsServer().GetCars();
      if (cars.Count() > 64)
        throw std::runtime_error("actor count exceeds bridge bound");
      bool first = true;
      for (int i = 0; i < cars.Count(); ++i) {
        auto car = cars.Get(i);
        if (car.IsNull())
          continue;
        if (!first)
          out << ',';
        first = false;
        out << "{\"car_index\":" << i << ",\"actor\":" << car_id(car)
            << ",\"player_alias\":" << identities.player(player_key(car))
            << '}';
      }
    }
    out << ']';
    return out.str();
  }
  std::string game_context() {
    // Read-only diagnostics, including on rejected commands. These do not
    // replace or relax offline() and never reinterpret a base event as a server.
    const bool online = gameWrapper->IsInOnlineGame();
    const bool replay = gameWrapper->IsInReplay();
    const bool in_game = gameWrapper->IsInGame();
    const bool freeplay = gameWrapper->IsInFreeplay();
    const bool training = gameWrapper->IsInCustomTraining();
    const bool server_null = gameWrapper->GetGameEventAsServer().IsNull();
    const bool current_null = gameWrapper->GetCurrentGameState().IsNull();
    auto car = gameWrapper->GetLocalCar();
    std::ostringstream out;
    out << std::boolalpha << "{\"IsInOnlineGame\":" << online
        << ",\"IsInReplay\":" << replay << ",\"IsInGame\":" << in_game
        << ",\"IsInFreeplay\":" << freeplay
        << ",\"IsInCustomTraining\":" << training
        << ",\"serverIsNull\":" << server_null
        << ",\"currentGameStateIsNull\":" << current_null
        << ",\"localCarIsNull\":" << car.IsNull()
        << ",\"baseGetGameEventIsNull\":";
    if (car.IsNull())
      out << "null";
    else
      out << car.GetGameEvent().IsNull();
    out << ",\"baseGetGameEventSource\":\"GetLocalCar().GetGameEvent()\""
        << ",\"base_event\":";
    if (car.IsNull() || car.GetGameEvent().IsNull())
      out << "null";
    else {
      auto event = car.GetGameEvent();
      auto cars = event.GetCars();
      bool listed = false;
      const bool bounded = cars.Count() >= 0 && cars.Count() <= 64;
      if (bounded)
        for (int i = 0; i < cars.Count(); ++i)
          listed |= cars.Get(i).memory_address == car.memory_address;
      // These are base wrapper queries only. In particular, raw role is not
      // silently equated to a qualified server/online authority decision.
      out << "{\"GetMatchTypeName\":"
          << json_string(event.GetMatchTypeName().substr(0, 128))
          << ",\"IsPlayingOffline\":" << event.IsPlayingOffline()
          << ",\"IsPlayingTraining\":" << event.IsPlayingTraining()
          << ",\"IsPlayingLan\":" << event.IsPlayingLan()
          << ",\"IsPlayingPrivate\":" << event.IsPlayingPrivate()
          << ",\"IsPlayingPublic\":" << event.IsPlayingPublic()
          << ",\"IsOnlineMultiplayer\":" << event.IsOnlineMultiplayer()
          << ",\"GetbMultiplayer\":" << bool(event.GetbMultiplayer())
          << ",\"GetRole\":" << unsigned(event.GetRole())
          << ",\"GetRemoteRole\":" << unsigned(event.GetRemoteRole())
          << ",\"car_count\":" << cars.Count()
          << ",\"local_player_count\":" << event.GetLocalPlayers().Count()
          << ",\"local_car_listed\":";
      if (bounded)
        out << listed;
      else
        out << "null";
      out << ",\"runtime_class_available\":false,\"runtime_class\":null"
          << ",\"server_HasAuthority_available\":false,\"server_HasAuthority\":null}";
    }
    out << ",\"typed_server_observation\":";
    if (!diagnostic_server)
      out << "null";
    else {
      // Do not dereference an old receiver from an acknowledgement. Retain the
      // SDK wrapper only for identity comparison, and expose callback readbacks
      // as dated observations, never as a gate authorization.
      out << "{\"source_hook\":" << json_string(diagnostic_server_hook)
          << ",\"observed_monotonic_ns\":" << diagnostic_server_observed_ns
          << ",\"age_ns\":" << now_ns()-diagnostic_server_observed_ns
          << ",\"HasAuthority_at_observation\":" << diagnostic_server_authority
          << ",\"identity_matches_local_car_event\":";
      if (car.IsNull() || car.GetGameEvent().IsNull())
        out << "null";
      else
        out << (diagnostic_server->memory_address == car.GetGameEvent().memory_address);
      out << ",\"runtime_class_available\":false}";
    }
    out
        << ",\"sdk_api_version\":" << BAKKESMOD_PLUGIN_API_VERSION
        << ",\"runtime_bakkesmod_version\":"
        << gameWrapper->GetBakkesModVersion()
        << ",\"offlineGateSnapshotAllowed\":"
        << (!online && !replay && (in_game || freeplay || training) && !server_null)
        << '}';
    return out.str();
  }
  void acknowledge(const std::string &nonce, const std::string &operation,
                   bool success, const std::string &error) {
    auto dir = command_dir();
    std::ostringstream out;
    out << "{\"schema\":\"noodle.instrument.command-result.v1\",\"nonce\":"
        << json_string(nonce) << ",\"operation\":" << json_string(operation)
        << ",\"success\":" << (success ? "true" : "false")
        << ",\"error\":" << json_string(error)
        << ",\"host_unix_ms\":" << unix_ms() << ',' << bridge_status() << '}';
    auto text = out.str();
    auto temporary = dir / "automation-result.tmp";
    auto target = dir / "automation-result.json";
    {
      std::ofstream file(temporary, std::ios::binary | std::ios::trunc);
      file << text << '\n';
      file.close();
      if (!file)
        throw std::runtime_error("cannot write command acknowledgement");
    }
    if (!MoveFileExW(temporary.c_str(), target.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
      throw std::runtime_error("cannot atomically publish acknowledgement");
    std::ofstream audit(dir / "automation-operations.jsonl", std::ios::app);
    audit << text << '\n';
  }
  void bridge_tick() {
    if (bridge_session.empty())
      return;
    auto dir = command_dir();
    try {
      auto marker =
          parse_enable(small_file(dir / "automation-enable.txt"), unix_ms());
      if (marker.session != bridge_session ||
          marker.expires_ms != bridge_expires)
        throw std::runtime_error("enable marker changed");
    } catch (const std::exception &) {
      bridge_session.clear();
      stop();
      return;
    }
    auto request = dir / "automation-request.txt";
    auto claimed = dir / "automation-request.processing";
    if (std::filesystem::exists(request)) {
      std::string nonce = "invalid", operation = "invalid";
      bool success = false;
      std::string error;
      if (!MoveFileExW(request.c_str(), claimed.c_str(),
                       MOVEFILE_WRITE_THROUGH)) {
        bridge_session.clear();
        stop();
        return;
      }
      try {
        auto text = small_file(claimed);
        std::istringstream peek(text);
        std::string magic;
        peek >> magic >> nonce;
        if (!token(nonce))
          nonce = "invalid";
        auto command = inbox.accept(text, unix_ms());
        nonce = command.nonce;
        operation = command.operation;
        if (command.operation != "stop" && !offline())
          throw std::runtime_error("offline local server required");
        if (command.operation == "start") {
          if (writer)
            throw std::runtime_error("capture already active");
          start();
        } else if (command.operation == "stop")
          stop();
        else if (command.operation == "tape")
          load_tape(command.filename);
        else if (command.operation == "prepare_fixed")
          queue_fixed();
        success = true;
      } catch (const std::exception &e) {
        error = e.what();
      }
      std::error_code ignored;
      std::filesystem::remove(claimed, ignored);
      try {
        acknowledge(nonce, operation, success, error);
      } catch (const std::exception &e) {
        cvarManager->log(e.what());
        bridge_session.clear();
        stop();
        return;
      }
    }
    auto token_guard = std::weak_ptr<std::atomic<bool>>(alive);
    auto session = bridge_session;
    gameWrapper->SetTimeout(
        [this, token_guard, session](GameWrapper *) {
          auto guard = token_guard.lock();
          if (guard && guard->load() && bridge_session == session)
            bridge_tick();
        },
        .1f);
  }
  void start_bridge() {
    try {
      auto marker = parse_enable(
          small_file(command_dir() / "automation-enable.txt"), unix_ms());
      bridge_session = marker.session;
      bridge_expires = marker.expires_ms;
      inbox.reset();
      bridge_tick();
    } catch (const std::exception &) {
      bridge_session.clear();
    } // Absent/empty marker leaves no polling task.
  }
  void load_tape(const std::string &name) {
    if (fixed_preparation.blocks_tape() || experiment_failed)
      throw std::runtime_error("native fixed preparation not invoked or failed; new capture required");
    if (!writer || !offline() ||
        std::filesystem::path(name).filename() != name ||
        name.find("..") != std::string::npos)
      throw std::runtime_error("capture first; tape filename only");
    std::ifstream source(std::filesystem::path(gameWrapper->GetDataFolderW()) /
                         "noodle-instrument" / name);
    std::string magic;
    Tape tape;
    unsigned count;
    source >> magic >> tape.duration >> count;
    if (!source || magic != "NNIT1" || count == 0 || count > 64 ||
        tape.duration == 0 || tape.duration > 72000)
      throw std::runtime_error("bad tape header");
    for (unsigned a = 0; a < count; ++a) {
      uint32_t actor;
      source >> actor;
      if (!actor || tape.actors.count(actor))
        throw std::runtime_error("bad actor id");
      auto &row = tape.actors[actor];
      row.resize(tape.duration);
      for (auto &control : row) {
        for (float &axis : control.axes)
          source >> axis;
        for (unsigned b = 0; b < 5; ++b) {
          int v;
          source >> v;
          if (!source || (v != 0 && v != 1))
            throw std::runtime_error("bad button");
          if (b == 4 && v)
            throw std::runtime_error("Jumped input is engine-derived");
          control.buttons[b] = v != 0;
        }
      }
    }
    source >> std::ws;
    if (!source.eof())
      throw std::runtime_error("extra tape data");
    tape.validate();
    for (auto &[actor, t] : tape.actors) {
      bool found = false;
      auto cars = gameWrapper->GetGameEventAsServer().GetCars();
      for (int i = 0; i < cars.Count(); ++i) {
        auto car = cars.Get(i);
        if (!car.IsNull())
          found |= car_id(car) == actor;
      }
      if (!found)
        throw std::runtime_error("unknown actor id");
    }
    tape_actors.clear();
    for (auto &[actor, t] : tape.actors)
      tape_actors.insert(actor);
    preparation_suspension.clear();
    controller.arm(std::move(tape), now_ns());
  }

public:
  void onLoad() override {
    // Both exact function names and ServerWrapper caller are listed by the
    // official Commonly Hooked Functions documentation. Countdown is the
    // documented freeplay-start/reset fallback when the game clock is idle.
    for (std::string hook : {server_time_hook, server_countdown_hook})
      gameWrapper->HookEventWithCallerPost<ServerWrapper>(
          hook, [this](ServerWrapper caller, void *, std::string function) {
            if (caller.IsNull())
              return;
            diagnostic_server = std::make_unique<ServerWrapper>(caller);
            diagnostic_server_authority = caller.HasAuthority();
            diagnostic_server_observed_ns = now_ns();
            diagnostic_server_hook = std::move(function);
          });
    cvarManager->registerNotifier(
        "noodle_capture_start",
        [this](std::vector<std::string>) {
          try {
            start();
          } catch (const std::exception &e) {
            cvarManager->log(e.what());
            stop();
          }
        },
        "Start bounded native qualification capture", PERMISSION_OFFLINE);
    cvarManager->registerNotifier(
        "noodle_actor_list",
        [this](std::vector<std::string>) {
          if (!offline())
            return;
          auto cars = gameWrapper->GetGameEventAsServer().GetCars();
          for (int i = 0; i < cars.Count(); ++i) {
            auto car = cars.Get(i);
            if (!car.IsNull())
              cvarManager->log("Noodle car index " + std::to_string(i) +
                               " actor " + std::to_string(car_id(car)));
          }
        },
        "List capture-local controlled car ids", PERMISSION_OFFLINE);
    cvarManager->registerNotifier(
        "noodle_capture_stop", [this](std::vector<std::string>) { stop(); },
        "Finish capture and neutralize controlled cars", PERMISSION_ALL);
    cvarManager->registerNotifier(
        "noodle_tape",
        [this](std::vector<std::string> a) {
          try {
            if (a.size() != 2)
              throw std::runtime_error("noodle_tape filename");
            load_tape(a[1]);
          } catch (const std::exception &e) {
            cvarManager->log(e.what());
            controller.stop();
            neutral();
            tape_actors.clear();
          }
        },
        "Arm settled-floor preparation + numeric actor tapes",
        PERMISSION_OFFLINE);
    gameWrapper->HookEventWithCaller<CarWrapper>(
        input_hook, [this](CarWrapper car, void *params, std::string) {
          if (!writer || neutralizing ||
              fixed_preparation.state() == FixedPreparationState::Applying)
            return;
          if (!offline()) {
            stop();
            return;
          }
          if (car.IsNull() || !params)
            return;
          ++callback_sequence;
          if (fixed_preparation.state() == FixedPreparationState::Queued)
            apply_fixed(car, *static_cast<ControllerInput *>(params));
          // Preserve a neutral preparation prefix through the first tape arm;
          // the ordinary controller then owns neutral settling and tape input.
          if (fixed_preparation.state() == FixedPreparationState::Invoked &&
              tape_actors.empty() && fixed_gate(car, Controls{}).allowed())
            write_input_parameter(*static_cast<ControllerInput *>(params), {});
          if (!tape_actors.count(car_id(car)))
            return;
          auto c = controller.get(car_id(car), frame(), now_ns(), true);
          // The SDK's SetVehicleInput guide explicitly documents mutation of
          // this ControllerInput* in the pre-hook. It is valid only during this
          // callback; no pointer is retained and no OverrideParams copy is used.
          auto input = static_cast<ControllerInput *>(params);
          write_input_parameter(*input, c);
          input_record(Kind::InputRequested, 1, car, controls(*input));
        });
    gameWrapper->HookEventWithCallerPost<CarWrapper>(
        input_hook, [this](CarWrapper car, void *params, std::string) {
          if (!writer || neutralizing ||
              fixed_preparation.state() == FixedPreparationState::Applying)
            return;
          if (!offline()) {
            stop();
            return;
          }
          if (car.IsNull())
            return;
          input_record(Kind::InputConsumed, 2, car, controls(car.GetInput()),
                       static_cast<const ControllerInput *>(params));
          auto f = frame();
          if (f != last_frame) {
            last_frame = f;
            world_sample();
          }
        });
    gameWrapper->HookEventPost(camera_hook, [this](std::string) {
      if (!writer)
        return;
      if (!offline()) {
        stop();
        return;
      }
      auto camera = gameWrapper->GetCamera();
      if (camera.IsNull())
        return;
      auto pov = camera.GetPOV();
      Fields f;
      vector(f, 1, pov.location);
      rotation(f, 2, pov.rotation);
      f.f(50, pov.FOV);
      rotation(f, 51, camera.GetCurrentSwivel());
      auto car = gameWrapper->GetLocalCar();
      auto target_actor = car.IsNull() ? 0 : car_id(car);
      f.u(52, target_actor); // Existing local-car ID, not verified native owner.
      f.u(200, !car.IsNull());
      f.u(201, target_actor);
      if (!car.IsNull()) {
        vector(f, 202, car.GetLocation());
        vector(f, 203, car.GetVelocity());
        vector(f, 204, car.GetAngularVelocity());
        rotation(f, 205, car.GetRotation());
        f.f(207, car.GetPhysicsTime());
        camera_target_rb_fields(f, 210, car.GetRBState());
        camera_target_rb_fields(f, 220, car.GetCurrentRBState());
        camera_target_rb_fields(f, 230, car.GetOldRBState());
      }
      auto engine = gameWrapper->GetEngine();
      f.u(206, !engine.IsNull());
      if (!engine.IsNull())
        f.f(240, engine.GetRenderAlpha());
      auto settings = camera.GetCameraSettings();
      f.f(120, settings.FOV);
      f.f(121, settings.Height);
      f.f(122, settings.Pitch);
      f.f(123, settings.Distance);
      f.f(124, settings.Stiffness);
      f.f(125, settings.SwivelSpeed);
      f.f(126, settings.TransitionSpeed);
      emit(Kind::Camera, 3, 0, std::move(f));
    });
    for (std::string event :
         {"Function TAGame.Car_TA.OnHitBall", "Function TAGame.Car_TA.Demolish",
          "Function TAGame.Car_TA.RespawnInPlace",
          "Function TAGame.Car_TA.eventOnGroundChanged"})
      gameWrapper->HookEventWithCallerPost<CarWrapper>(
          event, [this, event](CarWrapper car, void *, std::string) {
            if (!writer || !offline() || car.IsNull())
              return;
            Fields f;
            f.u(60, event.find("OnHitBall") != std::string::npos  ? 1
                    : event.find("Demolish") != std::string::npos ? 2
                    : event.find("Respawn") != std::string::npos  ? 3
                                                                  : 4);
            emit(Kind::Event, 2, car_id(car), std::move(f));
            if (event.find("Respawn") != std::string::npos) {
              if (tape_actors.count(car_id(car))) {
                experiment_failed = true;
                controller.stop();
                neutral();
                tape_actors.clear();
              }
              identities.retire(car.memory_address);
            }
          });
    gameWrapper->HookEventWithCallerPost<ActorWrapper>(
        "Function TAGame.VehiclePickup_Boost_TA.Pickup",
        [this](ActorWrapper pad, void *, std::string) { pad_sample(pad, 1); });
    gameWrapper->HookEvent("Function TAGame.GameEvent_Soccar_TA.Destroyed",
                           [this](std::string) {
                             stop();
                             diagnostic_server.reset();
                             identities.reset();
                             known_car_handles.clear();
                             discovered_pads.clear();
                             ++generation;
                           });
    start_bridge();
  }
  void onUnload() override {
    bridge_session.clear();
    *alive = false;
    stop();
    gameWrapper->UnhookEvent(input_hook);
    gameWrapper->UnhookEventPost(input_hook);
    gameWrapper->UnhookEventPost(camera_hook);
    gameWrapper->UnhookEventPost(server_time_hook);
    gameWrapper->UnhookEventPost(server_countdown_hook);
    diagnostic_server.reset();
    gameWrapper->UnhookEventPost(
        "Function TAGame.VehiclePickup_Boost_TA.Pickup");
    for (std::string event :
         {"Function TAGame.Car_TA.OnHitBall", "Function TAGame.Car_TA.Demolish",
          "Function TAGame.Car_TA.RespawnInPlace",
          "Function TAGame.Car_TA.eventOnGroundChanged"})
      gameWrapper->UnhookEventPost(event);
    gameWrapper->UnhookEvent("Function TAGame.GameEvent_Soccar_TA.Destroyed");
  }
};
BAKKESMOD_PLUGIN(RocketLaboratoryInstrument, "RocketLaboratory Offline Instrument (qualification)",
                 "0.1.0",
                 PLUGINTYPE_FREEPLAY | PLUGINTYPE_CUSTOM_TRAINING |
                     PLUGINTYPE_BOTAI)
