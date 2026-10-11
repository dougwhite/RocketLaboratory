#pragma once
// Original portable instrumentation core. No engine pointers cross this
// boundary.
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
namespace noodle::instrument {
inline uint64_t now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}
inline void uint_bytes(std::vector<uint8_t> &b, uint64_t v, unsigned n) {
  for (unsigned i = 0; i < n; ++i)
    b.push_back(uint8_t(v >> (8 * i)));
}
inline uint32_t crc32(const std::vector<uint8_t> &b) {
  uint32_t c = ~0u;
  for (auto x : b) {
    c ^= x;
    for (int i = 0; i < 8; ++i)
      c = (c >> 1) ^ ((0u - (c & 1u)) & 0xedb88320u);
  }
  return ~c;
}
enum class Kind : uint16_t {
  InputRequested = 1,
  InputConsumed = 2,
  Body = 3,
  Wheel = 4,
  Mechanic = 5,
  Camera = 6,
  Event = 7,
  Quality = 8,
  World = 9,
  Pad = 10
};
// TLV field: uint16 key, uint8 scalar type (1=f32,2=u32,3=i32), uint8 count,
// values.
struct Fields {
  std::vector<uint8_t> bytes;
  void header(uint16_t key, uint8_t type, uint8_t count) {
    uint_bytes(bytes, key, 2);
    bytes.push_back(type);
    bytes.push_back(count);
  }
  void f(uint16_t key, float x) {
    header(key, 1, 1);
    uint32_t u;
    static_assert(sizeof(float) == 4);
    std::memcpy(&u, &x, 4);
    uint_bytes(bytes, u, 4);
  }
  void u(uint16_t key, uint32_t x) {
    header(key, 2, 1);
    uint_bytes(bytes, x, 4);
  }
  void i(uint16_t key, int32_t x) {
    header(key, 3, 1);
    uint_bytes(bytes, uint32_t(x), 4);
  }
  void vec(uint16_t key, float x, float y, float z) {
    header(key, 1, 3);
    for (float v : {x, y, z}) {
      uint32_t u;
      std::memcpy(&u, &v, 4);
      uint_bytes(bytes, u, 4);
    }
  }
  void quat(uint16_t key, float w, float x, float y, float z) {
    header(key, 1, 4);
    for (float v : {w, x, y, z}) {
      uint32_t u;
      std::memcpy(&u, &v, 4);
      uint_bytes(bytes, u, 4);
    }
  }
  void rot(uint16_t key, int32_t p, int32_t y, int32_t r) {
    header(key, 3, 3);
    for (int32_t v : {p, y, r})
      uint_bytes(bytes, uint32_t(v), 4);
  }
};
// Public getter observations only. Invalid values stay absent; this is not a
// default hitbox, body-family lookup, asset mesh or qualified collision shape.
struct BodyConfiguration {
  int32_t loadout_body_id;
  std::array<float, 3> extent, offset;
  uint32_t valid_source_mask() const {
    uint32_t result = loadout_body_id > 0 ? 1u : 0u;
    bool valid_extent = true, valid_offset = true;
    for (float x : extent) valid_extent &= std::isfinite(x) && x > 0;
    for (float x : offset) valid_offset &= std::isfinite(x);
    if (valid_extent) result |= 2;
    if (valid_offset) result |= 4;
    return result;
  }
  void append(Fields &f, uint32_t actor, uint32_t registry_generation) const {
    if (!actor || !registry_generation)
      throw std::invalid_argument("body configuration requires capture actor identity and generation");
    const uint32_t mask = valid_source_mask();
    f.u(250, mask == 7); f.u(251, actor);
    if (mask & 1) f.i(252, loadout_body_id);
    if (mask & 2) f.vec(253, extent[0], extent[1], extent[2]);
    if (mask & 4) f.vec(254, offset[0], offset[1], offset[2]);
    f.u(255, mask); f.u(256, 7); f.u(257, registry_generation);
  }
};
// Public component configuration getters, not measured impulse/acceleration.
// Null components and invalid values remain separately observable and absent.
struct JumpConfiguration {
  bool component_present = false;
  std::array<float, 9> values{};
  static constexpr uint32_t all_sources = 511;
  uint32_t valid_source_mask() const {
    uint32_t mask = 0;
    if (component_present)
      for (unsigned i=0; i<values.size(); ++i)
        if (std::isfinite(values[i])) mask |= uint32_t(1) << i;
    return mask;
  }
  void append(Fields &f, uint32_t actor, uint32_t registry_generation) const {
    if (!actor || !registry_generation)
      throw std::invalid_argument("jump configuration requires capture actor identity and generation");
    const uint32_t mask = valid_source_mask();
    f.u(260, component_present); f.u(261, mask == all_sources); f.u(262, actor);
    f.u(263, component_present ? all_sources : 0); f.u(264, mask);
    f.u(265, registry_generation);
    for (unsigned i=0; i<values.size(); ++i)
      if (mask & (uint32_t(1) << i)) f.f(uint16_t(270+i), values[i]);
  }
};
struct Record {
  Kind kind;
  uint16_t phase;
  uint64_t host_ns;
  int64_t engine_frame;
  uint32_t actor;
  Fields fields;
};
// Phase identifiers describe hook location, not proven integration phase.
inline std::vector<uint8_t> encode(const Record &r, uint64_t sequence) {
  std::vector<uint8_t> b;
  uint_bytes(b, uint16_t(r.kind), 2);
  uint_bytes(b, r.phase, 2);
  uint_bytes(b, sequence, 8);
  uint_bytes(b, r.host_ns, 8);
  uint_bytes(b, uint64_t(r.engine_frame), 8);
  uint_bytes(b, r.actor, 4);
  b.insert(b.end(), r.fields.bytes.begin(), r.fields.bytes.end());
  std::vector<uint8_t> out;
  uint_bytes(out, b.size(), 4);
  uint_bytes(out, crc32(b), 4);
  out.insert(out.end(), b.begin(), b.end());
  return out;
}
class Writer {
  std::ofstream file;
  std::thread thread;
  std::mutex lock;
  std::condition_variable ready;
  std::deque<std::vector<uint8_t>> queue;
  size_t queued = 0, queue_limit;
  uint64_t budget, accepted = 8, sequence = 0;
  bool closing = false;
  std::atomic<bool> failed{false};
  std::atomic<uint64_t> dropped{0};
  void run() {
    for (;;) {
      std::vector<uint8_t> b;
      {
        std::unique_lock<std::mutex> g(lock);
        ready.wait(g, [&] { return closing || !queue.empty(); });
        if (queue.empty() && closing)
          break;
        b = std::move(queue.front());
        queue.pop_front();
        queued -= b.size();
      }
      file.write(reinterpret_cast<const char *>(b.data()), b.size());
      if (!file)
        failed = true;
    }
    file.flush();
    if (!file)
      failed = true;
  }

public:
  Writer(const std::string &path, size_t max_queue = 8 * 1024 * 1024,
         uint64_t max_bytes = 128 * 1024 * 1024)
      : file(path, std::ios::binary | std::ios::trunc), queue_limit(max_queue),
        budget(max_bytes) {
    if (!file)
      throw std::runtime_error("cannot open capture");
    file.write("NNI2\x01\x00\x00\x00", 8);
    thread = std::thread([this] { run(); });
  }
  bool submit(const Record &r) {
    std::lock_guard<std::mutex> g(lock);
    if (closing || failed) {
      ++dropped;
      return false;
    }
    auto b = encode(r, sequence++);
    if (b.size() > 65544 ||
        b.size() > queue_limit - std::min(queued, queue_limit) ||
        accepted + b.size() > budget) {
      ++dropped;
      failed = true;
      return false;
    }
    queued += b.size();
    accepted += b.size();
    queue.push_back(std::move(b));
    ready.notify_one();
    return true;
  }
  void close() {
    {
      std::lock_guard<std::mutex> g(lock);
      closing = true;
      ready.notify_one();
    }
    if (thread.joinable())
      thread.join();
    file.close();
  }
  ~Writer() { close(); }
  bool valid() const { return !failed && dropped == 0; }
  uint64_t drop_count() const { return dropped.load(); }
};
struct Controls {
  std::array<float, 7> axes{};
  std::array<bool, 5> buttons{};
  bool valid() const {
    for (auto a : axes)
      if (!std::isfinite(a) || std::abs(a) > 1)
        return false;
    return true;
  }
};
// Called only on the documented SDK hook parameter by the offline adapter.
// Field assignment avoids replacing padding or unrelated bits and deliberately
// preserves Jumped, which is engine history rather than an authored button.
template <class Input> void write_input_parameter(Input &input, Controls c) {
  input.Throttle = c.axes[0];
  input.Steer = c.axes[1];
  input.Pitch = c.axes[2];
  input.Yaw = c.axes[3];
  input.Roll = c.axes[4];
  input.DodgeForward = c.axes[5];
  input.DodgeStrafe = c.axes[6];
  input.Handbrake = c.buttons[0];
  input.Jump = c.buttons[1];
  input.ActivateBoost = c.buttons[2];
  input.HoldingBoost = c.buttons[3];
}
struct Tape {
  uint32_t duration = 0;
  std::map<uint32_t, std::vector<Controls>> actors;
  void validate() const {
    if (duration == 0 || duration > 72000 || actors.empty() ||
        actors.size() > 64)
      throw std::invalid_argument("invalid tape bounds");
    for (auto &[id, t] : actors) {
      if (id == 0 || t.size() != duration)
        throw std::invalid_argument("incomplete actor tape");
      for (auto &c : t)
        if (!c.valid())
          throw std::invalid_argument("nonfinite or out-of-range controls");
    }
  }
  Controls input(uint32_t actor, uint32_t tick) const {
    auto p = actors.find(actor);
    return p != actors.end() && tick < duration ? p->second[tick] : Controls{};
  }
};
// Native handles stay private. A reused handle/fingerprint change gets a new
// incarnation.
inline uint64_t unix_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
inline bool token(const std::string &text, size_t max_size = 64) {
  if (text.empty() || text.size() > max_size)
    return false;
  for (unsigned char c : text)
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
          (c >= '0' && c <= '9') || c == '-' || c == '_'))
      return false;
  return true;
}
inline uint64_t decimal_u64(const std::string &text) {
  if (text.empty() || text.size() > 20)
    throw std::invalid_argument("bad timestamp");
  uint64_t result = 0;
  for (unsigned char c : text) {
    if (c < '0' || c > '9' || result > (UINT64_MAX - (c - '0')) / 10)
      throw std::invalid_argument("bad timestamp");
    result = result * 10 + (c - '0');
  }
  return result;
}
struct EnableMarker {
  std::string session;
  uint64_t expires_ms;
};
inline EnableMarker parse_enable(const std::string &text, uint64_t now) {
  if (text.empty() || text.size() > 1024)
    throw std::invalid_argument("invalid enable marker size");
  std::istringstream in(text);
  std::string magic, session, time, extra;
  in >> magic >> session >> time;
  if (!in || in >> extra || magic != "NNIA1" || !token(session))
    throw std::invalid_argument("invalid enable marker");
  auto expires = decimal_u64(time);
  if (expires <= now || expires - now > 300000)
    throw std::invalid_argument(
        "enable marker expired or exceeds five minutes");
  return {session, expires};
}
struct Command {
  std::string nonce, operation, filename;
  uint64_t issued_ms;
};
class CommandInbox {
  std::set<std::string> consumed;

public:
  Command accept(const std::string &text, uint64_t now) {
    if (text.empty() || text.size() > 1024)
      throw std::invalid_argument("invalid request size");
    std::istringstream in(text);
    std::string magic, time;
    Command c;
    in >> magic >> c.nonce >> time >> c.operation;
    if (!in || magic != "NNIC1" || !token(c.nonce))
      throw std::invalid_argument("invalid request header");
    if (c.operation == "tape") {
      in >> c.filename;
      if (!in || c.filename.size() > 96 || c.filename.empty() ||
          c.filename[0] == '.' || c.filename.find("..") != std::string::npos)
        throw std::invalid_argument("invalid tape filename");
      for (unsigned char x : c.filename)
        if (!((x >= 'A' && x <= 'Z') || (x >= 'a' && x <= 'z') ||
              (x >= '0' && x <= '9') || x == '-' || x == '_' || x == '.'))
          throw std::invalid_argument("invalid tape filename");
    } else if (c.operation != "start" && c.operation != "stop" &&
               c.operation != "actors" && c.operation != "prepare_fixed")
      throw std::invalid_argument("unknown operation");
    std::string extra;
    if (in >> extra)
      throw std::invalid_argument("extra request tokens");
    c.issued_ms = decimal_u64(time);
    if ((now >= c.issued_ms && now - c.issued_ms > 10000) ||
        (c.issued_ms > now && c.issued_ms - now > 5000))
      throw std::invalid_argument("stale or future request");
    if (consumed.count(c.nonce))
      throw std::invalid_argument("duplicate request nonce");
    if (consumed.size() >= 1024)
      throw std::invalid_argument("command session request limit");
    consumed.insert(c.nonce);
    return c;
  }
  void reset() { consumed.clear(); }
};
class IdentityRegistry {
  struct Entry {
    std::string fingerprint;
    uint32_t actor;
  };
  std::map<uintptr_t, Entry> active;
  std::map<std::string, uint32_t> players;
  uint32_t next_actor = 1, next_player = 1;

public:
  uint32_t observe(uintptr_t handle, const std::string &fingerprint) {
    if (!handle)
      return 0;
    auto it = active.find(handle);
    if (it != active.end() && it->second.fingerprint == fingerprint)
      return it->second.actor;
    auto actor = next_actor++;
    active[handle] = {fingerprint, actor};
    return actor;
  }
  uint32_t player(const std::string &private_key) {
    if (private_key.empty())
      return 0;
    auto [it, new_entry] = players.emplace(private_key, next_player);
    if (new_entry)
      ++next_player;
    return it->second;
  }
  uint32_t lookup(uintptr_t handle) const {
    auto it = active.find(handle);
    return it == active.end() ? 0 : it->second.actor;
  }
  void retire(uintptr_t handle) { active.erase(handle); }
  void reset() {
    active.clear();
    players.clear();
    next_actor = 1;
    next_player = 1;
  }
};
enum class Stage { Idle, Preparing, Running, Done, Failed };
// Only the fixed experiment is supported. This core has no engine addresses,
// setters or configurable pose; the adapter supplies observed gate booleans.
inline bool authored_neutral(const Controls &c) {
  if (!c.valid()) return false;
  for (auto a : c.axes) if (a != 0.f) return false;
  for (unsigned i = 0; i < 4; ++i) if (c.buttons[i]) return false;
  return true; // Jumped is engine-derived, not an authored control.
}
enum class FixedPreparationState { Idle, Queued, Applying, Invoked, Failed, Cancelled };
struct FixedPreparationGate {
  bool offline = false, capture_active = false, capture_valid = false;
  bool single_local_actor = false, identity_matches = false;
  bool map_matches = false, generation_matches = false, tape_empty = false;
  bool neutral = false;
  Stage controller_stage = Stage::Idle;
  bool allowed() const {
    return offline && capture_active && capture_valid && single_local_actor &&
           identity_matches && map_matches && generation_matches && tape_empty &&
           neutral && controller_stage == Stage::Idle;
  }
};
class FixedPreparation {
  FixedPreparationState state_ = FixedPreparationState::Idle;
  uint64_t deadline_ = 0;
  int64_t applied_frame_ = -1;
  uint64_t applied_sequence_ = 0;
public:
  static constexpr const char *recipe = "native_actor_fixed_pose_v1";
  static constexpr uint64_t max_queue_ns = 2000000000ull;
  static constexpr std::array<float, 3> location = {0.f, -3000.f, 200.f};
  void reset() { state_ = FixedPreparationState::Idle; deadline_ = 0;
                 applied_frame_ = -1; applied_sequence_ = 0; }
  void queue(uint64_t now, const FixedPreparationGate &gate) {
    if (state_ != FixedPreparationState::Idle || !gate.allowed() ||
        now > UINT64_MAX - max_queue_ns)
      throw std::invalid_argument("fixed preparation requires unused capture, neutral single local actor and idle offline Stadium");
    deadline_ = now + max_queue_ns;
    state_ = FixedPreparationState::Queued;
  }
  bool check(uint64_t now, const FixedPreparationGate &gate) {
    if (state_ != FixedPreparationState::Queued) return false;
    if (now > deadline_ || !gate.allowed()) {
      state_ = FixedPreparationState::Failed;
      return false;
    }
    return true;
  }
  bool begin(uint64_t now, const FixedPreparationGate &gate, int64_t frame,
             uint64_t sequence) {
    if (!check(now, gate)) return false;
    if (frame < 0 || !sequence || sequence > UINT32_MAX) {
      state_ = FixedPreparationState::Failed;
      return false;
    }
    // Consume before calling setters. Exceptions must not permit a retry.
    state_ = FixedPreparationState::Applying;
    applied_frame_ = frame; applied_sequence_ = sequence;
    return true;
  }
  void invoked() {
    if (state_ != FixedPreparationState::Applying)
      throw std::logic_error("fixed preparation was not applying");
    state_ = FixedPreparationState::Invoked;
  }
  void fail() { if (state_ != FixedPreparationState::Idle &&
                    state_ != FixedPreparationState::Cancelled)
                 state_ = FixedPreparationState::Failed; }
  void cancel() { if (state_ == FixedPreparationState::Queued ||
                      state_ == FixedPreparationState::Applying)
                   state_ = FixedPreparationState::Cancelled; }
  bool blocks_tape() const { return state_ != FixedPreparationState::Idle &&
                                   state_ != FixedPreparationState::Invoked; }
  FixedPreparationState state() const { return state_; }
  int64_t applied_frame() const { return applied_frame_; }
  uint64_t applied_sequence() const { return applied_sequence_; }
  const char *name() const {
    switch(state_) {
    case FixedPreparationState::Idle: return "idle";
    case FixedPreparationState::Queued: return "queued";
    case FixedPreparationState::Applying: return "applying";
    case FixedPreparationState::Invoked: return "applied_setters_unverified";
    case FixedPreparationState::Failed: return "failed";
    case FixedPreparationState::Cancelled: return "cancelled";
    }
    return "failed";
  }
};
class Controller {
  Tape tape;
  Stage stage_ = Stage::Idle;
  uint32_t stable = 0, required_stable;
  int64_t last_frame = -1, start_frame = -1;
  uint64_t heartbeat = 0, deadline = 0;
  uint32_t tick_ = 0;

public:
  explicit Controller(uint32_t settle_steps = 120)
      : required_stable(settle_steps) {
    if (!settle_steps)
      throw std::invalid_argument("empty preparation");
  }
  void arm(Tape t, uint64_t now, uint64_t max_ns = 120000000000ull) {
    t.validate();
    tape = std::move(t);
    stage_ = Stage::Preparing;
    stable = 0;
    last_frame = -1;
    start_frame = -1;
    heartbeat = now;
    deadline = now + max_ns;
    tick_ = 0;
  }
  // Adapter submits one *candidate* frame. Qualification decides whether these
  // frames are physics steps.
  bool advance(int64_t frame, bool predicates, uint64_t now, bool offline) {
    if (stage_ != Stage::Preparing && stage_ != Stage::Running)
      return false;
    if (!offline || now > deadline ||
        (last_frame >= 0 && frame != last_frame + 1)) {
      stage_ = Stage::Failed;
      return false;
    }
    if (frame < 0) {
      stage_ = Stage::Failed;
      return false;
    }
    heartbeat = now;
    last_frame = frame;
    if (stage_ == Stage::Preparing) {
      stable = predicates ? stable + 1 : 0;
      if (stable >= required_stable) {
        stage_ = Stage::Running;
        start_frame = frame + 1;
      }
      return true;
    }
    tick_ = uint32_t(frame - start_frame);
    if (tick_ >= tape.duration)
      stage_ = Stage::Done;
    return true;
  }
  Controls get(uint32_t actor, int64_t frame, uint64_t now, bool offline) {
    if (!offline || now > deadline || now - heartbeat > 1000000000ull) {
      if (stage_ == Stage::Running || stage_ == Stage::Preparing)
        stage_ = Stage::Failed;
      return {};
    }
    if (stage_ != Stage::Running || frame < start_frame)
      return {};
    auto tick = uint64_t(frame - start_frame);
    if (tick >= tape.duration)
      return {};
    return tape.input(actor, uint32_t(tick));
  }
  void stop() { stage_ = Stage::Idle; }
  Stage stage() const { return stage_; }
  uint32_t tick() const { return tick_; }
};
} // namespace noodle::instrument
