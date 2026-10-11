#include "core.hpp"
#include <cassert>
#include <filesystem>
#include <limits>
using namespace noodle::instrument;
int main() {
  auto body_config_fields = [](const BodyConfiguration &config) {
    Fields f; config.append(f, 7, 1);
    std::map<uint16_t, std::vector<uint8_t>> values;
    for (size_t pos=0; pos<f.bytes.size();) {
      uint16_t key=uint16_t(f.bytes[pos]) | (uint16_t(f.bytes[pos+1])<<8);
      size_t size=4*size_t(f.bytes[pos+3]);
      values[key]={f.bytes.begin()+pos+4,f.bytes.begin()+pos+4+size};
      pos+=4+size;
    }
    return values;
  };
  BodyConfiguration config{123, {60.f,40.f,15.f}, {-5.f,0.f,20.f}};
  auto configured=body_config_fields(config);
  assert(config.valid_source_mask()==7 && configured.at(250)[0]==1);
  assert(configured.at(251)[0]==7 && configured.at(252)[0]==123);
  assert(configured.at(253).size()==12 && configured.at(254).size()==12);
  assert(configured.at(255)[0]==7 && configured.at(256)[0]==7 && configured.at(257)[0]==1);
  for (int32_t id : {-1,0}) {
    auto absent=config; absent.loadout_body_id=id;
    auto fields=body_config_fields(absent);
    assert(absent.valid_source_mask()==6 && !fields.count(252) && fields.at(250)[0]==0);
  }
  for (float invalid : {0.f,-1.f,std::numeric_limits<float>::infinity(),std::numeric_limits<float>::quiet_NaN()}) {
    auto absent=config; absent.extent[1]=invalid;
    auto fields=body_config_fields(absent);
    assert(absent.valid_source_mask()==5 && !fields.count(253) && fields.at(250)[0]==0);
  }
  auto missing_offset=config;missing_offset.offset[2]=std::numeric_limits<float>::quiet_NaN();
  assert(missing_offset.valid_source_mask()==3 && !body_config_fields(missing_offset).count(254));
  for (auto identity : {std::pair<uint32_t,uint32_t>{0,1},{1,0}}) {
    bool fail=false;
    try { Fields f;config.append(f,identity.first,identity.second); }
    catch(const std::invalid_argument &) { fail=true; }
    assert(fail);
  }
  auto jump_config_fields = [](const JumpConfiguration &config) {
    Fields f; config.append(f, 7, 1);
    std::map<uint16_t, std::vector<uint8_t>> values;
    for (size_t pos=0; pos<f.bytes.size();) {
      uint16_t key=uint16_t(f.bytes[pos]) | (uint16_t(f.bytes[pos+1])<<8);
      size_t size=4*size_t(f.bytes[pos+3]);
      values[key]={f.bytes.begin()+pos+4,f.bytes.begin()+pos+4+size};
      pos+=4+size;
    }
    return values;
  };
  JumpConfiguration jump_config{true, {.025f, -100.f, 0.f, .2f, .3f, 291.f, 1458.f, 300.f, .8f}};
  auto jumps=jump_config_fields(jump_config);
  assert(jump_config.valid_source_mask()==511 && jumps.at(260)[0]==1 && jumps.at(261)[0]==1);
  assert(jumps.at(262)[0]==7 && jumps.at(263)[0]==255 && jumps.at(263)[1]==1);
  assert(jumps.at(264)[0]==255 && jumps.at(264)[1]==1 && jumps.at(265)[0]==1);
  for (unsigned i=0;i<9;++i) assert(jumps.at(uint16_t(270+i)).size()==4);
  auto null_jump=jump_config;null_jump.component_present=false;
  jumps=jump_config_fields(null_jump);
  assert(null_jump.valid_source_mask()==0 && jumps.at(260)[0]==0 && jumps.at(261)[0]==0);
  assert(jumps.at(263)[0]==0 && jumps.at(264)[0]==0);
  for (unsigned i=0;i<9;++i) assert(!jumps.count(uint16_t(270+i)));
  for (unsigned i=0;i<9;++i)
    for (float invalid : {std::numeric_limits<float>::infinity(),std::numeric_limits<float>::quiet_NaN()}) {
      auto partial=jump_config;partial.values[i]=invalid;
      jumps=jump_config_fields(partial);
      assert(partial.valid_source_mask()==(511u & ~(1u<<i)) && !jumps.count(uint16_t(270+i)));
      assert(jumps.at(260)[0]==1 && jumps.at(261)[0]==0);
    }
  for (auto identity : {std::pair<uint32_t,uint32_t>{0,1},{1,0}}) {
    bool fail=false;
    try {Fields f;jump_config.append(f,identity.first,identity.second);}
    catch(const std::invalid_argument &) {fail=true;}
    assert(fail);
  }
  const uint64_t clock = 1000000;
  struct InputParameter {
    float Throttle, Steer, Pitch, Yaw, Roll, DodgeForward, DodgeStrafe;
    unsigned Handbrake : 1, Jump : 1, ActivateBoost : 1, HoldingBoost : 1,
        Jumped : 1, unrelated : 1;
  } input{};
  input.Jumped = 1;
  input.unrelated = 1;
  Controls authored;
  authored.axes = {1, .5f, -.25f, .125f, -.5f, .75f, -1};
  authored.buttons = {true, true, true, true, false};
  write_input_parameter(input, authored);
  assert(input.Throttle == 1 && input.Steer == .5f && input.Pitch == -.25f &&
         input.Yaw == .125f && input.Roll == -.5f && input.DodgeForward == .75f &&
         input.DodgeStrafe == -1);
  assert(input.Jumped && input.unrelated && input.Handbrake && input.Jump &&
         input.ActivateBoost && input.HoldingBoost);
  write_input_parameter(input, {});
  assert(input.Throttle == 0 && input.Steer == 0 && !input.Handbrake &&
         !input.Jump && !input.ActivateBoost && !input.HoldingBoost);
  assert(input.Jumped && input.unrelated);
  auto marker = parse_enable("NNIA1 lab_test 1300000", clock);
  assert(marker.session == "lab_test");
  for (std::string text :
       {"", "NNIA1 bad/path 1300000", "NNIA1 session 999999",
        "NNIA1 session 1300001", "NNIA1 session 1300000 extra"}) {
    bool fail = false;
    try {
      parse_enable(text, clock);
    } catch (const std::invalid_argument &) {
      fail = true;
    }
    assert(fail);
  }
  CommandInbox inbox;
  auto command = inbox.accept("NNIC1 one 1000000 start", clock);
  assert(command.operation == "start");
  assert(
      inbox.accept("NNIC1 two 1000000 tape half_flip.nnit", clock).filename ==
      "half_flip.nnit");
  assert(inbox.accept("NNIC1 three 995000 stop", clock).operation == "stop");
  for (std::string text : std::vector<std::string>{
           "NNIC1 one 1000000 start", "NNIC1 four 989999 actors",
           "NNIC1 four 1005001 actors", "NNIC1 four 1000000 console quit",
           "NNIC1 four 1000000 tape ../evil",
           "NNIC1 four 1000000 tape C:\\evil", "NNIC1 four 1000000 start extra",
           "NNIC1 bad! 1000000 actors",
           "NNIC1 four 18446744073709551616 actors", std::string(1025, 'x')}) {
    bool fail = false;
    try {
      inbox.accept(text, clock);
    } catch (const std::invalid_argument &) {
      fail = true;
    }
    assert(fail);
  }
  assert(inbox.accept("NNIC1 four 1000000 actors", clock).operation ==
         "actors");
  assert(inbox.accept("NNIC1 fixed 1000000 prepare_fixed", clock).operation ==
         "prepare_fixed");
  for (auto text : {"NNIC1 fixed2 1000000 prepare_fixed 1",
                    "NNIC1 fixed2 1000000 prepare_fixed arbitrary.json"}) {
    bool fail = false;
    try { inbox.accept(text, clock); } catch (const std::invalid_argument &) { fail = true; }
    assert(fail); // Fixed command cannot carry actor IDs, paths or coordinates.
  }

  FixedPreparationGate fixed_gate;
  fixed_gate.offline = fixed_gate.capture_active = fixed_gate.capture_valid = true;
  fixed_gate.single_local_actor = fixed_gate.identity_matches = true;
  fixed_gate.map_matches = fixed_gate.generation_matches = true;
  fixed_gate.tape_empty = fixed_gate.neutral = true;
  assert(fixed_gate.allowed());
  for (auto member : {&FixedPreparationGate::offline,
                      &FixedPreparationGate::capture_active,
                      &FixedPreparationGate::capture_valid,
                      &FixedPreparationGate::single_local_actor,
                      &FixedPreparationGate::identity_matches,
                      &FixedPreparationGate::map_matches,
                      &FixedPreparationGate::generation_matches,
                      &FixedPreparationGate::tape_empty,
                      &FixedPreparationGate::neutral}) {
    auto bad_gate = fixed_gate; bad_gate.*member = false;
    FixedPreparation p;
    bool rejected_gate = false;
    try { p.queue(clock, bad_gate); } catch (const std::invalid_argument &) { rejected_gate = true; }
    assert(rejected_gate && p.state() == FixedPreparationState::Idle);
    p.queue(clock, fixed_gate);
    assert(!p.begin(clock + 1, bad_gate, 1, 1));
    assert(p.state() == FixedPreparationState::Failed && p.blocks_tape());
    assert(!p.begin(clock + 2, fixed_gate, 2, 2)); // Never retry on gate recovery.
  }
  for (auto stage : {Stage::Preparing, Stage::Running, Stage::Done, Stage::Failed}) {
    auto bad_gate = fixed_gate; bad_gate.controller_stage = stage;
    assert(!bad_gate.allowed());
  }
  Controls neutral_controls;
  neutral_controls.buttons[4] = true; // Engine Jumped is not an authored input.
  assert(authored_neutral(neutral_controls));
  for (size_t i = 0; i < 7; ++i) {
    auto nonneutral = neutral_controls; nonneutral.axes[i] = .001f;
    assert(!authored_neutral(nonneutral));
  }
  for (size_t i = 0; i < 4; ++i) {
    auto nonneutral = neutral_controls; nonneutral.buttons[i] = true;
    assert(!authored_neutral(nonneutral));
  }
  neutral_controls.axes[0] = std::numeric_limits<float>::quiet_NaN();
  assert(!authored_neutral(neutral_controls));
  FixedPreparation fixed;
  assert(!fixed.blocks_tape());
  fixed.queue(clock, fixed_gate);
  assert(fixed.blocks_tape() && fixed.applied_frame() == -1);
  assert(fixed.begin(clock + FixedPreparation::max_queue_ns, fixed_gate, 42, 7));
  assert(fixed.state() == FixedPreparationState::Applying && fixed.blocks_tape());
  assert(!fixed.begin(clock + 1, fixed_gate, 43, 8));
  fixed.invoked();
  assert(!fixed.blocks_tape() && fixed.applied_frame() == 42 && fixed.applied_sequence() == 7);
  bool duplicate_preparation = false;
  try { fixed.queue(clock + 3, fixed_gate); } catch (const std::invalid_argument &) { duplicate_preparation = true; }
  assert(duplicate_preparation);
  fixed.reset(); fixed.queue(clock, fixed_gate);
  assert(!fixed.begin(clock + FixedPreparation::max_queue_ns + 1, fixed_gate, 42, 7));
  assert(fixed.state() == FixedPreparationState::Failed);
  fixed.reset(); fixed.queue(clock, fixed_gate); fixed.cancel();
  assert(fixed.state() == FixedPreparationState::Cancelled && fixed.blocks_tape());
  fixed.reset(); fixed.queue(clock, fixed_gate);
  assert(fixed.begin(clock + 1, fixed_gate, 42, 7));
  fixed.fail(); // Partial setter exception is terminal, never re-applied.
  assert(!fixed.begin(clock + 2, fixed_gate, 43, 8) && fixed.blocks_tape());
  for (auto bad : {std::pair<int64_t, uint64_t>{-1, 1}, {1, 0}, {1, uint64_t(UINT32_MAX) + 1}}) {
    fixed.reset(); fixed.queue(clock, fixed_gate);
    assert(!fixed.begin(clock + 1, fixed_gate, bad.first, bad.second));
    assert(fixed.blocks_tape());
  }
  fixed.reset();
  bool overflow_rejected = false;
  try { fixed.queue(UINT64_MAX, fixed_gate); } catch (const std::invalid_argument &) { overflow_rejected = true; }
  assert(overflow_rejected);

  IdentityRegistry identities;
  auto a = identities.observe(100, "player A");
  assert(a == identities.observe(100, "player A"));
  assert(a != identities.observe(100, "player B"));
  assert(identities.player("secret A") == identities.player("secret A"));
  assert(identities.player("secret A") != identities.player("secret B"));
  auto b = identities.lookup(100);
  identities.retire(100);
  assert(identities.lookup(100) == 0);
  assert(b != identities.observe(100, "player B"));
  assert(identities.observe(0, "empty") == 0);

  Tape t;
  t.duration = 3;
  t.actors[1].resize(3);
  t.actors[2].resize(3);
  t.actors[1][0].buttons[1] = true;
  t.actors[2][1].axes[0] = 1;
  t.validate();
  Controller c(2);
  c.arm(t, 100);
  assert(c.advance(40, true, 101, true));
  assert(c.stage() == Stage::Preparing);
  assert(c.advance(41, false, 102, true));
  assert(c.advance(42, true, 103, true));
  assert(c.advance(43, true, 104, true));
  assert(c.stage() == Stage::Running);
  assert(!c.get(1, 43, 105, true).buttons[1]);
  assert(c.get(1, 44, 105, true).buttons[1]);
  assert(c.get(2, 45, 105, true).axes[0] == 1);
  assert(!c.get(2, 44, 105, true).buttons[1]);
  assert(!c.get(99, 44, 105, true).buttons[1]);
  assert(c.advance(44, true, 106, true));
  assert(c.advance(45, true, 107, true));
  assert(c.advance(46, true, 108, true));
  assert(c.advance(47, true, 109, true));
  assert(c.stage() == Stage::Done);
  assert(!c.get(1, 48, 110, true).buttons[1]);
  c.arm(t, 200);
  c.advance(50, true, 201, true);
  assert(!c.advance(52, true, 202, true));
  assert(c.stage() == Stage::Failed);
  c.arm(t, 200);
  c.advance(50, true, 201, true);
  assert(!c.get(1, 51, 1000000300, true).buttons[1]);
  assert(c.stage() == Stage::Failed);
  c.arm(t, 200);
  assert(!c.advance(50, true, 201, false));
  assert(c.stage() == Stage::Failed);
  auto bad = t;
  bad.actors[1][0].axes[0] = std::numeric_limits<float>::quiet_NaN();
  bool rejected = false;
  try {
    bad.validate();
  } catch (const std::invalid_argument &) {
    rejected = true;
  }
  assert(rejected);
  Fields f;
  Fields quaternion;
  quaternion.quat(6, .5f, -.5f, .5f, -.5f);
  assert(quaternion.bytes.size() == 20);
  assert(quaternion.bytes[0] == 6 && quaternion.bytes[1] == 0 &&
         quaternion.bytes[2] == 1 && quaternion.bytes[3] == 4);
  // IEEE float32 WXYZ signs/order retained, rather than integer rotation.
  assert(quaternion.bytes[7] == 0x3f && quaternion.bytes[11] == 0xbf &&
         quaternion.bytes[15] == 0x3f && quaternion.bytes[19] == 0xbf);
  f.vec(1, 1.25f, 2, 3);
  f.i(2, -100);
  f.u(3, 42);
  Record r{Kind::Body, 2, 1234, 78, 3, f};
  auto encoded = encode(r, 0);
  assert(encoded.size() == 8 + 32 + 32);
  assert(crc32({}) == 0);
  std::string path = "core-test.nni";
  {
    Writer w(path, 1024, 4096);
    assert(w.submit(r));
    w.close();
    assert(w.valid());
  }
  assert(std::filesystem::file_size(path) == 8 + encoded.size());
  std::filesystem::remove(path);
  {
    Writer w(path, 1, 4096);
    assert(!w.submit(r));
    w.close();
    assert(!w.valid() && w.drop_count() == 1);
  }
  std::filesystem::remove(path);
  {
    Writer w(path, 1024, 8);
    assert(!w.submit(r));
    w.close();
    assert(!w.valid());
  }
  std::filesystem::remove(path);
}
