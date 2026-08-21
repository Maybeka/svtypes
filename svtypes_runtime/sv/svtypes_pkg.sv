package svtypes_pkg;
  timeunit 1ns;
  timeprecision 1ps;

  parameter int unsigned SVTYPES_MAX_DYNAMIC_LENGTH = 1_000_000;

  class encoding_descriptor;
    string unified_type_name;
    string encoding_fingerprint;
    int unsigned binary_format_version;

    function new(
      string unified_type_name = "",
      string encoding_fingerprint = "",
      int unsigned binary_format_version = 1
    );
      this.unified_type_name = unified_type_name;
      this.encoding_fingerprint = encoding_fingerprint;
      this.binary_format_version = binary_format_version;
    endfunction

    function bit is_compatible(encoding_descriptor other);
      return other != null &&
             unified_type_name == other.unified_type_name &&
             encoding_fingerprint == other.encoding_fingerprint &&
             binary_format_version == other.binary_format_version;
    endfunction
  endclass

  function automatic void require_encoding_compatible(
    encoding_descriptor expected,
    encoding_descriptor received
  );
    if (expected == null || received == null) begin
      $fatal(2, "SvTypes encoding descriptor cannot be null");
    end
    if (expected.binary_format_version != received.binary_format_version) begin
      $fatal(2, "SvTypes binary format mismatch: expected %0d got %0d",
             expected.binary_format_version, received.binary_format_version);
    end
    if (expected.unified_type_name != received.unified_type_name) begin
      $fatal(2, "SvTypes unified type mismatch: expected %s got %s",
             expected.unified_type_name, received.unified_type_name);
    end
    if (expected.encoding_fingerprint != received.encoding_fingerprint) begin
      $fatal(2, "SvTypes encoding fingerprint mismatch for %s", expected.unified_type_name);
    end
  endfunction

  class runtime_capabilities;
    int unsigned package_major_version;
    int unsigned schema_format_version;
    int unsigned binary_format_version;
    int unsigned object_envelope_version;
    int unsigned generator_runtime_abi_version;
    string provided[$];

    function new();
      package_major_version = 1;
      schema_format_version = 1;
      binary_format_version = 1;
      object_envelope_version = 2;
      generator_runtime_abi_version = 1;
      provided = '{
        "svtypes.checked-encoding-descriptor.v1",
        "svtypes.codec-context.v1",
        "svtypes.constraint-ir.v1",
        "svtypes.constraint-sample.v1",
        "svtypes.record-schema.v1",
        "svtypes.remote-reference.v1"
      };
    endfunction

    function bit provides(string required_name);
      foreach (provided[index]) begin
        if (provided[index] == required_name) return 1'b1;
      end
      return 1'b0;
    endfunction
  endclass

  function automatic runtime_capabilities get_runtime_capabilities();
    runtime_capabilities result;
    result = new();
    return result;
  endfunction

  function automatic void require_runtime_compatible(
    string required[$],
    runtime_capabilities received,
    runtime_capabilities expected = null
  );
    if (received == null) $fatal(2, "SvTypes runtime capabilities cannot be null");
    if (expected == null) expected = get_runtime_capabilities();
    if (expected.package_major_version != received.package_major_version ||
        expected.schema_format_version != received.schema_format_version ||
        expected.binary_format_version != received.binary_format_version ||
        expected.object_envelope_version != received.object_envelope_version ||
        expected.generator_runtime_abi_version != received.generator_runtime_abi_version) begin
      $fatal(2, "SvTypes runtime compatibility version mismatch");
    end
    foreach (required[index]) begin
      if (!received.provides(required[index])) begin
        $fatal(2, "SvTypes runtime missing required capability: %s", required[index]);
      end
    end
  endfunction

  // Base class for all modeled objects
  virtual class sv_object;
    longint unsigned __svtypes_object_number;

    function new();
      __svtypes_object_number = allocate_object_number();
    endfunction

    function void ensure_svtypes_object_number();
      if (__svtypes_object_number == 0) begin
        __svtypes_object_number = allocate_object_number();
      end
    endfunction

    pure virtual function void pack(ref byte unsigned bytes[$]);
    pure virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    pure virtual function void pack_body(ref byte unsigned bytes[$]);
    pure virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    pure virtual function string svtypes_sprint();
    pure virtual function void svtypes_display();
  endclass

  // --- Helpers ---
  localparam longint unsigned SVTYPES_OBJECT_NUMBER_ORIGIN = 64'h0002_0000_0000_0000;

  class codec_session;
    longint unsigned next_object_counter;
`ifdef SVTYPES_USE_WEAK_REFERENCE
    weak_reference#(sv_object) object_registry[longint unsigned];
`else
    sv_object object_registry[longint unsigned];
`endif

    function new(longint unsigned counter_start = 1);
      next_object_counter = counter_start;
    endfunction

    function longint unsigned allocate_object_number();
      longint unsigned number;
      number = SVTYPES_OBJECT_NUMBER_ORIGIN | next_object_counter;
      next_object_counter++;
      return number;
    endfunction

    function void reset_object_number_allocator(longint unsigned start = 1);
      next_object_counter = start;
    endfunction

    function void clear();
      object_registry.delete();
    endfunction
  endclass

  codec_session default_codec_session;
  codec_session active_codec_session;
  codec_session codec_session_stack[$];

  function automatic codec_session current_codec_session();
    if (default_codec_session == null) begin
      default_codec_session = new();
    end
    if (active_codec_session == null) begin
      active_codec_session = default_codec_session;
    end
    return active_codec_session;
  endfunction

  function automatic void begin_codec_session(codec_session session);
    if (session == null) $fatal(2, "SvTypes codec session cannot be null");
    codec_session_stack.push_back(current_codec_session());
    active_codec_session = session;
  endfunction

  function automatic void end_codec_session();
    if (codec_session_stack.size() == 0) begin
      $fatal(2, "SvTypes codec session stack underflow");
    end
    active_codec_session = codec_session_stack.pop_back();
  endfunction

  function automatic longint unsigned allocate_object_number();
    return current_codec_session().allocate_object_number();
  endfunction

  function automatic void reset_object_number_allocator(longint unsigned start = 1);
    current_codec_session().reset_object_number_allocator(start);
  endfunction

`ifdef SVTYPES_USE_WEAK_REFERENCE
  weak_reference#(sv_object) object_registry[longint unsigned];
`else
  sv_object object_registry[longint unsigned];
`endif
  class svtypes_pack_context;
    bit seen[longint unsigned];
  endclass
  svtypes_pack_context svtypes_pack_context_stack[$];
  bit svtypes_dump_seen[longint unsigned];
  int unsigned svtypes_dump_depth = 0;
  bit svtypes_plusarg_seen[longint unsigned];
  int unsigned svtypes_plusarg_depth = 0;

  function automatic bit begin_dump_object(longint unsigned object_number);
    bit repeated;
    if (svtypes_dump_depth == 0) begin
      svtypes_dump_seen.delete();
    end
    repeated = svtypes_dump_seen.exists(object_number);
    svtypes_dump_seen[object_number] = 1'b1;
    svtypes_dump_depth++;
    return repeated;
  endfunction

  function automatic void end_dump_object();
    if (svtypes_dump_depth > 0) begin
      svtypes_dump_depth--;
    end
    if (svtypes_dump_depth == 0) begin
      svtypes_dump_seen.delete();
    end
  endfunction

  function automatic bit begin_plusarg_object(longint unsigned object_number);
    bit repeated;
    if (svtypes_plusarg_depth == 0) begin
      svtypes_plusarg_seen.delete();
    end
    repeated = svtypes_plusarg_seen.exists(object_number);
    svtypes_plusarg_seen[object_number] = 1'b1;
    svtypes_plusarg_depth++;
    return repeated;
  endfunction

  function automatic void end_plusarg_object();
    if (svtypes_plusarg_depth > 0) begin
      svtypes_plusarg_depth--;
    end
    if (svtypes_plusarg_depth == 0) begin
      svtypes_plusarg_seen.delete();
    end
  endfunction

  function automatic string escape_dump_string(string value);
    string result = "\"";
    for (int i = 0; i < value.len(); i++) begin
      case (value[i])
        byte'("\\"): result = {result, "\\\\"};
        byte'("\""): result = {result, "\\\""};
        byte'("\n"): result = {result, "\\n"};
        byte'("\r"): result = {result, "\\r"};
        byte'("\t"): result = {result, "\\t"};
        default: result = {result, string'(value[i])};
      endcase
    end
    return {result, "\""};
  endfunction

  function automatic void register_object(sv_object obj);
    if (obj != null && obj.__svtypes_object_number != 0) begin
`ifdef SVTYPES_USE_WEAK_REFERENCE
      current_codec_session().object_registry[obj.__svtypes_object_number] = new(obj);
`else
      current_codec_session().object_registry[obj.__svtypes_object_number] = obj;
`endif
    end
  endfunction

  function automatic sv_object get_object(longint unsigned object_number);
    if (current_codec_session().object_registry.exists(object_number)) begin
`ifdef SVTYPES_USE_WEAK_REFERENCE
      return current_codec_session().object_registry[object_number].get();
`else
      return current_codec_session().object_registry[object_number];
`endif
    end
    return null;
  endfunction

  function automatic int unsigned prune_object_registry();
    int unsigned removed = 0;
`ifdef SVTYPES_USE_WEAK_REFERENCE
    foreach (current_codec_session().object_registry[object_number]) begin
      if (current_codec_session().object_registry[object_number].get() == null) begin
        current_codec_session().object_registry.delete(object_number);
        removed++;
      end
    end
`endif
    return removed;
  endfunction

  function automatic void clear_object_registry();
    current_codec_session().clear();
  endfunction

  function automatic bit unregister_object(longint unsigned object_number);
    if (!current_codec_session().object_registry.exists(object_number)) begin
      return 1'b0;
    end
    current_codec_session().object_registry.delete(object_number);
    return 1'b1;
  endfunction

  class remote_ref;
    string target_type_name;
    longint unsigned object_number;

    function new(string target_type_name = "", longint unsigned object_number = 0);
      this.target_type_name = target_type_name;
      this.object_number = object_number;
    endfunction

    function bit is_null();
      return object_number == 0;
    endfunction
  endclass

  class remote_ref_packer;
    static function void pack(remote_ref value, ref byte unsigned bytes[$]);
      longint unsigned object_number = value == null ? 0 : value.object_number;
      for (int i = 0; i < 8; i++) begin
        bytes.push_back(object_number[i * 8 +: 8]);
      end
    endfunction

    static function void unpack(ref remote_ref value, ref byte unsigned bytes[$], ref int offset);
      longint unsigned object_number = '0;
      require_available(bytes, offset, 8, "RemoteRef");
      for (int i = 0; i < 8; i++) begin
        object_number[i * 8 +: 8] = bytes[offset + i];
      end
      offset += 8;
      if (value == null) begin
        value = new("", object_number);
      end else begin
        value.object_number = object_number;
      end
    endfunction
  endclass

  function automatic void begin_pack_graph();
    svtypes_pack_context pack_ctx = new();
    svtypes_pack_context_stack.push_back(pack_ctx);
  endfunction

  function automatic void end_pack_graph();
    if (svtypes_pack_context_stack.size() == 0) begin
      $fatal(2, "SvTypes pack context stack underflow");
    end
    void'(svtypes_pack_context_stack.pop_back());
  endfunction

  function automatic void pack_object_reference(longint unsigned object_number, ref byte unsigned bytes[$]);
    bytes.push_back(8'h02);
    for (int i = 0; i < 8; i++) begin
      bytes.push_back(object_number[i * 8 +: 8]);
    end
  endfunction

  function automatic longint unsigned unpack_object_reference(ref byte unsigned bytes[$], ref int offset);
    longint unsigned object_number = '0;
    require_available(bytes, offset, 8, "object reference");
    for (int i = 0; i < 8; i++) begin
      object_number[i * 8 +: 8] = bytes[offset + i];
    end
    offset += 8;
    if (object_number == 0) begin
      $fatal(2, "SvTypes object reference has id 0");
    end
    return object_number;
  endfunction

  function automatic longint unsigned peek_object_number(ref byte unsigned bytes[$], input int offset);
    longint unsigned object_number = '0;
    require_available(bytes, offset, 16, "object header peek");
    if (bytes[offset] != "S" || bytes[offset + 1] != "V" ||
        bytes[offset + 2] != "X" || bytes[offset + 3] != "O") begin
      $fatal(2, "SvTypes object header magic mismatch at offset=%0d", offset);
    end
    for (int i = 0; i < 8; i++) begin
      object_number[i * 8 +: 8] = bytes[offset + 8 + i];
    end
    if (object_number == 0) begin
      $fatal(2, "SvTypes non-null object envelope has id 0");
    end
    return object_number;
  endfunction

  function automatic void pack_object_value(sv_object value, ref byte unsigned bytes[$]);
    bit implicit_context;
    svtypes_pack_context pack_ctx;
    implicit_context = svtypes_pack_context_stack.size() == 0;
    if (implicit_context) begin_pack_graph();
    pack_ctx = svtypes_pack_context_stack[$];
    if (value == null) begin
      bytes.push_back(8'h00);
      if (implicit_context) end_pack_graph();
      return;
    end
    value.ensure_svtypes_object_number();
    register_object(value);
    if (pack_ctx.seen.exists(value.__svtypes_object_number)) begin
      pack_object_reference(value.__svtypes_object_number, bytes);
      if (implicit_context) end_pack_graph();
      return;
    end
    pack_ctx.seen[value.__svtypes_object_number] = 1'b1;
    bytes.push_back(8'h01);
    value.pack_body(bytes);
    if (implicit_context) end_pack_graph();
  endfunction

  function automatic void require_available(
    ref byte unsigned bytes[$],
    input int offset,
    input int unsigned byte_count,
    input string where
  );
    if (offset < 0 || offset + byte_count > bytes.size()) begin
      $fatal(
        2,
        "SvTypes unpack underflow in %s: offset=%0d need=%0d size=%0d",
        where,
        offset,
        byte_count,
        bytes.size()
      );
    end
  endfunction

  // Pack a 32-bit LE length header
  function automatic void pack_length(int unsigned len, ref byte unsigned bytes[$]);
    if (len > SVTYPES_MAX_DYNAMIC_LENGTH) begin
      $fatal(2, "SvTypes dynamic length %0d exceeds encoder limit %0d", len, SVTYPES_MAX_DYNAMIC_LENGTH);
    end
    bytes.push_back(len[ 7: 0]);
    bytes.push_back(len[15: 8]);
    bytes.push_back(len[23:16]);
    bytes.push_back(len[31:24]);
  endfunction

  function automatic void pack_object_header(
    string type_name,
    string encoding_fingerprint,
    int unsigned field_count,
    longint unsigned object_number,
    ref byte unsigned bytes[$]
  );
    bytes.push_back("S");
    bytes.push_back("V");
    bytes.push_back("X");
    bytes.push_back("O");
    bytes.push_back(8'h02);
    bytes.push_back(8'h00);
    bytes.push_back(field_count[7:0]);
    bytes.push_back(field_count[15:8]);
    for (int i = 0; i < 8; i++) begin
      bytes.push_back(object_number[i * 8 +: 8]);
    end
    pack_string(type_name, bytes);
    if (encoding_fingerprint.len() != 64) begin
      $fatal(2, "SvTypes encoding fingerprint must contain 64 hexadecimal digits");
    end
    for (int i = 0; i < 32; i++) begin
      int unsigned high = hex_nibble(encoding_fingerprint[i * 2]);
      int unsigned low = hex_nibble(encoding_fingerprint[i * 2 + 1]);
      bytes.push_back(byte'((high << 4) | low));
    end
  endfunction

  function automatic void unpack_object_header(
    string expected_type_name,
    string expected_encoding_fingerprint,
    int unsigned expected_field_count,
    output longint unsigned object_number,
    ref byte unsigned bytes[$],
    ref int offset
  );
    byte unsigned version;
    int unsigned field_count;
    string type_name;
    require_available(bytes, offset, 16, "object header");
    if (bytes[offset] != "S" || bytes[offset + 1] != "V" ||
        bytes[offset + 2] != "X" || bytes[offset + 3] != "O") begin
      $fatal(2, "SvTypes object header magic mismatch at offset=%0d", offset);
    end
    version = bytes[offset + 4];
    if (version != 8'h02 || bytes[offset + 5] != 8'h00) begin
      $fatal(2, "SvTypes object header version mismatch at offset=%0d", offset);
    end
    field_count = {16'h0, bytes[offset + 7], bytes[offset + 6]};
    object_number = '0;
    for (int i = 0; i < 8; i++) begin
      object_number[i * 8 +: 8] = bytes[offset + 8 + i];
    end
    if (object_number == 0) begin
      $fatal(2, "SvTypes non-null object envelope has id 0");
    end
    offset += 16;
    unpack_string(type_name, bytes, offset);
    if (type_name != expected_type_name) begin
      $fatal(2, "SvTypes object type mismatch: expected %s got %s", expected_type_name, type_name);
    end
    if (field_count != expected_field_count) begin
      $fatal(2, "SvTypes object field-count mismatch for %s: expected %0d got %0d",
             expected_type_name, expected_field_count, field_count);
    end
    if (expected_encoding_fingerprint.len() != 64) begin
      $fatal(2, "SvTypes expected encoding fingerprint must contain 64 hexadecimal digits");
    end
    require_available(bytes, offset, 32, "object encoding fingerprint");
    for (int i = 0; i < 32; i++) begin
      byte unsigned expected_byte;
      expected_byte = byte'((hex_nibble(expected_encoding_fingerprint[i * 2]) << 4) |
                            hex_nibble(expected_encoding_fingerprint[i * 2 + 1]));
      if (bytes[offset + i] != expected_byte) begin
        $fatal(2, "SvTypes object encoding fingerprint mismatch for %s", expected_type_name);
      end
    end
    offset += 32;
  endfunction

  function automatic int unsigned hex_nibble(byte unsigned value);
    if (value >= "0" && value <= "9") return value - "0";
    if (value >= "a" && value <= "f") return value - "a" + 10;
    $fatal(2, "SvTypes encoding fingerprint is not lowercase hexadecimal");
    return 0;
  endfunction

  function automatic int unsigned unpack_length(ref byte unsigned bytes[$], ref int offset);
    int unsigned len;
    require_available(bytes, offset, 4, "length");
    len = {bytes[offset+3], bytes[offset+2], bytes[offset+1], bytes[offset]};
    offset += 4;
    if (len > SVTYPES_MAX_DYNAMIC_LENGTH) begin
      $fatal(2, "SvTypes dynamic length %0d exceeds decoder limit %0d", len, SVTYPES_MAX_DYNAMIC_LENGTH);
    end
    return len;
  endfunction

  function automatic void pack_integral(
    longint unsigned value,
    int unsigned byte_count,
    ref byte unsigned bytes[$]
  );
    for (int i = 0; i < byte_count; i++) begin
      bytes.push_back(value[i * 8 +: 8]);
    end
  endfunction

  function automatic longint unsigned unpack_integral(
    int unsigned byte_count,
    ref byte unsigned bytes[$],
    ref int offset
  );
    longint unsigned value = '0;
    require_available(bytes, offset, byte_count, "integral");
    for (int i = 0; i < byte_count; i++) begin
      value[i * 8 +: 8] = bytes[offset + i];
    end
    offset += byte_count;
    return value;
  endfunction

  class bit_packer #(type T = bit);
    localparam int WIDTH = $bits(T);
    localparam int BYTE_COUNT = (WIDTH + 7) / 8;
    localparam int STORAGE_WIDTH = BYTE_COUNT * 8;

    static function void pack(
      input T value,
      ref byte unsigned bytes[$]
    );
      bit [STORAGE_WIDTH-1:0] storage;
      storage = '0;
      storage[WIDTH-1:0] = value;
      for (int i = 0; i < BYTE_COUNT; i++) begin
        bytes.push_back(storage[i * 8 +: 8]);
      end
    endfunction

    static function void unpack(
      ref T value,
      ref byte unsigned bytes[$],
      ref int offset
    );
      bit [STORAGE_WIDTH-1:0] storage;
      storage = '0;
      require_available(bytes, offset, BYTE_COUNT, "bits");
      for (int i = 0; i < BYTE_COUNT; i++) begin
        storage[i * 8 +: 8] = bytes[offset + i];
      end
      offset += BYTE_COUNT;
      value = T'(storage[WIDTH-1:0]);
    endfunction
  endclass

  class logic_packer #(type T = logic);
    static function void pack(input T value, ref byte unsigned bytes[$]);
      logic [$bits(T)-1:0] flat_value;
      byte unsigned value_bytes[$];
      byte unsigned x_bytes[$];
      byte unsigned z_bytes[$];
      int unsigned byte_count = ($bits(T) + 7) / 8;
      repeat (byte_count) begin
        value_bytes.push_back(0);
        x_bytes.push_back(0);
        z_bytes.push_back(0);
      end
      flat_value = value;
      for (int i = 0; i < $bits(T); i++) begin
        case (flat_value[i])
          1'b1: value_bytes[i / 8][i % 8] = 1'b1;
          1'bx: x_bytes[i / 8][i % 8] = 1'b1;
          1'bz: z_bytes[i / 8][i % 8] = 1'b1;
          default: ;
        endcase
      end
      foreach (value_bytes[i]) bytes.push_back(value_bytes[i]);
      foreach (x_bytes[i]) bytes.push_back(x_bytes[i]);
      foreach (z_bytes[i]) bytes.push_back(z_bytes[i]);
    endfunction

    static function void unpack(ref T value, ref byte unsigned bytes[$], ref int offset);
      logic [$bits(T)-1:0] flat_value;
      int unsigned byte_count = ($bits(T) + 7) / 8;
      require_available(bytes, offset, byte_count * 3, "four-state packed value");
      flat_value = '0;
      for (int i = 0; i < $bits(T); i++) begin
        bit value_bit = bytes[offset + i / 8][i % 8];
        bit x_bit = bytes[offset + byte_count + i / 8][i % 8];
        bit z_bit = bytes[offset + byte_count * 2 + i / 8][i % 8];
        if (x_bit) flat_value[i] = 1'bx;
        else if (z_bit) flat_value[i] = 1'bz;
        else flat_value[i] = value_bit;
      end
      value = T'(flat_value);
      offset += byte_count * 3;
    endfunction
  endclass

  class fixed_array_packer #(
    type T = int,
    int SIZE = 1,
    type ELEM_PACKER = bit_packer#(int)
  );
    static function void pack(input T value[SIZE], ref byte unsigned bytes[$]);
      foreach (value[i]) begin
        ELEM_PACKER::pack(value[i], bytes);
      end
    endfunction

    static function void unpack(ref T value[SIZE], ref byte unsigned bytes[$], ref int offset);
      foreach (value[i]) begin
        ELEM_PACKER::unpack(value[i], bytes, offset);
      end
    endfunction
  endclass

  class dyn_array_packer #(
    type T = int,
    type ELEM_PACKER = bit_packer#(int)
  );
    static function void pack(ref T value[], ref byte unsigned bytes[$]);
      pack_length(value.size(), bytes);
      foreach (value[i]) begin
        ELEM_PACKER::pack(value[i], bytes);
      end
    endfunction

    static function void unpack(ref T value[], ref byte unsigned bytes[$], ref int offset);
      int unsigned len = unpack_length(bytes, offset);
      value = new[len];
      foreach (value[i]) begin
        ELEM_PACKER::unpack(value[i], bytes, offset);
      end
    endfunction
  endclass

  class queue_packer #(
    type T = int,
    type ELEM_PACKER = bit_packer#(int)
  );
    static function void pack(ref T value[$], ref byte unsigned bytes[$]);
      pack_length(value.size(), bytes);
      foreach (value[i]) begin
        ELEM_PACKER::pack(value[i], bytes);
      end
    endfunction

    static function void unpack(ref T value[$], ref byte unsigned bytes[$], ref int offset);
      int unsigned len = unpack_length(bytes, offset);
      value.delete();
      for (int i = 0; i < len; i++) begin
        T tmp;
        ELEM_PACKER::unpack(tmp, bytes, offset);
        value.push_back(tmp);
      end
    endfunction
  endclass

  class assoc_array_packer #(
    type K = int,
    type V = int,
    type KEY_PACKER = bit_packer#(int),
    type VALUE_PACKER = bit_packer#(int)
  );
    static function bit encoded_key_less(input K lhs, input K rhs);
      byte unsigned lhs_bytes[$];
      byte unsigned rhs_bytes[$];
      int unsigned common;
      KEY_PACKER::pack(lhs, lhs_bytes);
      KEY_PACKER::pack(rhs, rhs_bytes);
      common = lhs_bytes.size() < rhs_bytes.size() ? lhs_bytes.size() : rhs_bytes.size();
      for (int i = 0; i < common; i++) begin
        if (lhs_bytes[i] < rhs_bytes[i]) return 1'b1;
        if (lhs_bytes[i] > rhs_bytes[i]) return 1'b0;
      end
      return lhs_bytes.size() < rhs_bytes.size();
    endfunction

    static function void pack(ref V value[K], ref byte unsigned bytes[$]);
      K keys[$];
      K temporary;
      pack_length(value.size(), bytes);
      foreach (value[key]) begin
        keys.push_back(key);
      end
      for (int i = 0; i < keys.size(); i++) begin
        for (int j = i + 1; j < keys.size(); j++) begin
          if (encoded_key_less(keys[j], keys[i])) begin
            temporary = keys[i];
            keys[i] = keys[j];
            keys[j] = temporary;
          end
        end
      end
      foreach (keys[i]) begin
        KEY_PACKER::pack(keys[i], bytes);
        VALUE_PACKER::pack(value[keys[i]], bytes);
      end
    endfunction

    static function void unpack(ref V value[K], ref byte unsigned bytes[$], ref int offset);
      int unsigned len = unpack_length(bytes, offset);
      K key;
      V val;
      value.delete();
      for (int i = 0; i < len; i++) begin
        KEY_PACKER::unpack(key, bytes, offset);
        VALUE_PACKER::unpack(val, bytes, offset);
        value[key] = val;
      end
    endfunction
  endclass

  class object_packer #(type T = sv_object);
    static function void pack(T value, ref byte unsigned bytes[$]);
      pack_object_value(value, bytes);
    endfunction

    static function void unpack(ref T value, ref byte unsigned bytes[$], ref int offset);
      byte unsigned present;
      longint unsigned object_number;
      sv_object ref_obj;
      T typed_ref;
      require_available(bytes, offset, 1, "object presence");
      present = bytes[offset];
      offset += 1;
      if (present == 8'h00) begin
        value = null;
        return;
      end
      if (present == 8'h02) begin
        object_number = unpack_object_reference(bytes, offset);
        ref_obj = get_object(object_number);
        if (ref_obj == null) begin
          $fatal(2, "SvTypes unresolved object reference id %0d", object_number);
        end
        if (!$cast(typed_ref, ref_obj)) begin
          $fatal(2, "SvTypes object reference type mismatch for id %0d", object_number);
        end
        value = typed_ref;
        return;
      end
      if (present != 8'h01) begin
        $fatal(2, "SvTypes invalid object presence marker: %0d", present);
      end
      object_number = peek_object_number(bytes, offset);
      ref_obj = get_object(object_number);
      if (ref_obj != null) begin
        if (!$cast(typed_ref, ref_obj)) begin
          $fatal(2, "SvTypes object number collision type mismatch for id %0d", object_number);
        end
        value = typed_ref;
      end else if (value == null) begin
        value = new();
      end
      value.unpack_body(bytes, offset);
    endfunction
  endclass

  function automatic void pack_string(string value, ref byte unsigned bytes[$]);
    pack_length(value.len(), bytes);
    for (int i = 0; i < value.len(); i++) begin
      bytes.push_back(value[i]);
    end
  endfunction

  function automatic void unpack_string(
    ref string value,
    ref byte unsigned bytes[$],
    ref int offset
  );
    int unsigned len = unpack_length(bytes, offset);
    require_available(bytes, offset, len, "string");
    value = "";
    for (int i = 0; i < len; i++) begin
      value = {value, string'(bytes[offset + i])};
    end
    offset += len;
  endfunction

  class string_packer;
    static function void pack(input string value, ref byte unsigned bytes[$]);
      pack_string(value, bytes);
    endfunction

    static function void unpack(ref string value, ref byte unsigned bytes[$], ref int offset);
      unpack_string(value, bytes, offset);
    endfunction
  endclass

  class int_packer;
    static function void pack(input int value, ref byte unsigned bytes[$]);
      bit_packer#(int)::pack(value, bytes);
    endfunction

    static function void unpack(ref int value, ref byte unsigned bytes[$], ref int offset);
      bit_packer#(int)::unpack(value, bytes, offset);
    endfunction
  endclass

  class longint_packer;
    static function void pack(input longint value, ref byte unsigned bytes[$]);
      bit_packer#(longint)::pack(value, bytes);
    endfunction

    static function void unpack(ref longint value, ref byte unsigned bytes[$], ref int offset);
      bit_packer#(longint)::unpack(value, bytes, offset);
    endfunction
  endclass

  function automatic void pack_real(real value, ref byte unsigned bytes[$]);
    pack_integral($realtobits(value), 8, bytes);
  endfunction

  function automatic void unpack_real(
    ref real value,
    ref byte unsigned bytes[$],
    ref int offset
  );
    value = $bitstoreal(unpack_integral(8, bytes, offset));
  endfunction

  class real_packer;
    static function void pack(input real value, ref byte unsigned bytes[$]);
      pack_real(value, bytes);
    endfunction

    static function void unpack(ref real value, ref byte unsigned bytes[$], ref int offset);
      unpack_real(value, bytes, offset);
    endfunction
  endclass

  function automatic void pack_shortreal(shortreal value, ref byte unsigned bytes[$]);
    pack_integral($shortrealtobits(value), 4, bytes);
  endfunction

  function automatic void unpack_shortreal(
    ref shortreal value,
    ref byte unsigned bytes[$],
    ref int offset
  );
    value = $bitstoshortreal(int'(unpack_integral(4, bytes, offset)));
  endfunction

  class shortreal_packer;
    static function void pack(input shortreal value, ref byte unsigned bytes[$]);
      pack_shortreal(value, bytes);
    endfunction

    static function void unpack(ref shortreal value, ref byte unsigned bytes[$], ref int offset);
      unpack_shortreal(value, bytes, offset);
    endfunction
  endclass

  // Integer (int) - 4 bytes LE
  function automatic void pack_int(int value, ref byte unsigned bytes[$]);
    int_packer::pack(value, bytes);
  endfunction

  function automatic void unpack_int(ref int value, ref byte unsigned bytes[$], ref int offset);
    int_packer::unpack(value, bytes, offset);
  endfunction

  // Longint (longint) - 8 bytes LE
  function automatic void pack_longint(longint value, ref byte unsigned bytes[$]);
    longint_packer::pack(value, bytes);
  endfunction

  function automatic void unpack_longint(ref longint value, ref byte unsigned bytes[$], ref int offset);
    longint_packer::unpack(value, bytes, offset);
  endfunction

  // Nested Object
  function automatic void pack_object(sv_object value, ref byte unsigned bytes[$]);
    value.pack(bytes);
  endfunction

  function automatic void unpack_object(sv_object value, ref byte unsigned bytes[$], ref int offset);
    value.unpack(bytes, offset);
  endfunction

endpackage
