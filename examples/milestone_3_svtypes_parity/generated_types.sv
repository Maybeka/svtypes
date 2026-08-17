// Generated from examples.milestone_3_svtypes_parity.tests.types
// Do not edit by hand.

typedef enum int {
  RED = 0,
  GREEN = 1,
  BLUE = 2
} Color;

class Inner extends svtypes_pkg::sv_object;
  int a;
  bit [7:0] b;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("Inner", 2, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(a, bytes);
    svtypes_pkg::bits_packer#(bit [7:0])::pack(b, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing Inner object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing Inner object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("Inner", 2, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(a, bytes, offset);
    svtypes_pkg::bits_packer#(bit [7:0])::unpack(b, bytes, offset);
  endfunction
endclass

class BaseTx extends svtypes_pkg::sv_object;
  int id;
  int data [2];
  string label;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("BaseTx", 3, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(id, bytes);
    svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::pack(data, bytes);
    svtypes_pkg::string_packer::pack(label, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing BaseTx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing BaseTx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("BaseTx", 3, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(id, bytes, offset);
    svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::unpack(data, bytes, offset);
    svtypes_pkg::string_packer::unpack(label, bytes, offset);
  endfunction
endclass

class M3Tx extends BaseTx;
  bit [15:0] addr;
  longint serial;
  Color color;
  real ratio;
  shortreal temp;
  int dyn [];
  int q [$];
  Inner inner;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("M3Tx", 11, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(id, bytes);
    svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::pack(data, bytes);
    svtypes_pkg::string_packer::pack(label, bytes);
    svtypes_pkg::bits_packer#(bit [15:0])::pack(addr, bytes);
    svtypes_pkg::longint_packer::pack(serial, bytes);
    svtypes_pkg::bits_packer#(Color)::pack(color, bytes);
    svtypes_pkg::real_packer::pack(ratio, bytes);
    svtypes_pkg::shortreal_packer::pack(temp, bytes);
    svtypes_pkg::dyn_array_packer#(int, svtypes_pkg::int_packer)::pack(dyn, bytes);
    svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)::pack(q, bytes);
    svtypes_pkg::object_packer#(Inner)::pack(inner, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing M3Tx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing M3Tx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("M3Tx", 11, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(id, bytes, offset);
    svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::unpack(data, bytes, offset);
    svtypes_pkg::string_packer::unpack(label, bytes, offset);
    svtypes_pkg::bits_packer#(bit [15:0])::unpack(addr, bytes, offset);
    svtypes_pkg::longint_packer::unpack(serial, bytes, offset);
    svtypes_pkg::bits_packer#(Color)::unpack(color, bytes, offset);
    svtypes_pkg::real_packer::unpack(ratio, bytes, offset);
    svtypes_pkg::shortreal_packer::unpack(temp, bytes, offset);
    svtypes_pkg::dyn_array_packer#(int, svtypes_pkg::int_packer)::unpack(dyn, bytes, offset);
    svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)::unpack(q, bytes, offset);
    svtypes_pkg::object_packer#(Inner)::unpack(inner, bytes, offset);
  endfunction
endclass

class LargeMixedTx extends svtypes_pkg::sv_object;
  string tag;
  int header [4];
  int payload [];
  int samples [$];
  bit [15:0] flags [];
  bit [63:0] marker;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("LargeMixedTx", 6, __svtypes_object_number, bytes);
    svtypes_pkg::string_packer::pack(tag, bytes);
    svtypes_pkg::fixed_array_packer#(int, 4, svtypes_pkg::int_packer)::pack(header, bytes);
    svtypes_pkg::dyn_array_packer#(int, svtypes_pkg::int_packer)::pack(payload, bytes);
    svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)::pack(samples, bytes);
    svtypes_pkg::dyn_array_packer#(bit [15:0], svtypes_pkg::bits_packer#(bit [15:0]))::pack(flags, bytes);
    svtypes_pkg::bits_packer#(bit [63:0])::pack(marker, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing LargeMixedTx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing LargeMixedTx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("LargeMixedTx", 6, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::string_packer::unpack(tag, bytes, offset);
    svtypes_pkg::fixed_array_packer#(int, 4, svtypes_pkg::int_packer)::unpack(header, bytes, offset);
    svtypes_pkg::dyn_array_packer#(int, svtypes_pkg::int_packer)::unpack(payload, bytes, offset);
    svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)::unpack(samples, bytes, offset);
    svtypes_pkg::dyn_array_packer#(bit [15:0], svtypes_pkg::bits_packer#(bit [15:0]))::unpack(flags, bytes, offset);
    svtypes_pkg::bits_packer#(bit [63:0])::unpack(marker, bytes, offset);
  endfunction
endclass

class ManyTypesTx extends svtypes_pkg::sv_object;
  bit [0:0] u1;
  bit [6:0] u7;
  bit [8:0] u9;
  bit [32:0] u33;
  bit [64:0] u65;
  bit signed [4:0] s5;
  bit signed [11:0] s12;
  int i32;
  longint i64;
  Color color;
  string text;
  real fp64;
  shortreal fp32;
  bit [2:0] fixed_bits [4];
  bit signed [5:0] fixed_signed [3];
  int matrix [2] [2];
  bit [9:0] dyn_bits [];
  bit signed [8:0] dyn_signed [];
  Color q_colors [$];
  int assoc [string];
  Inner q_inner [$];
  Inner inner_arr [2];
  Inner nested;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("ManyTypesTx", 23, __svtypes_object_number, bytes);
    svtypes_pkg::bits_packer#(bit [0:0])::pack(u1, bytes);
    svtypes_pkg::bits_packer#(bit [6:0])::pack(u7, bytes);
    svtypes_pkg::bits_packer#(bit [8:0])::pack(u9, bytes);
    svtypes_pkg::bits_packer#(bit [32:0])::pack(u33, bytes);
    svtypes_pkg::bits_packer#(bit [64:0])::pack(u65, bytes);
    svtypes_pkg::bits_packer#(bit signed [4:0])::pack(s5, bytes);
    svtypes_pkg::bits_packer#(bit signed [11:0])::pack(s12, bytes);
    svtypes_pkg::int_packer::pack(i32, bytes);
    svtypes_pkg::longint_packer::pack(i64, bytes);
    svtypes_pkg::bits_packer#(Color)::pack(color, bytes);
    svtypes_pkg::string_packer::pack(text, bytes);
    svtypes_pkg::real_packer::pack(fp64, bytes);
    svtypes_pkg::shortreal_packer::pack(fp32, bytes);
    svtypes_pkg::fixed_array_packer#(bit [2:0], 4, svtypes_pkg::bits_packer#(bit [2:0]))::pack(fixed_bits, bytes);
    svtypes_pkg::fixed_array_packer#(bit signed [5:0], 3, svtypes_pkg::bits_packer#(bit signed [5:0]))::pack(fixed_signed, bytes);
    foreach (matrix[i]) begin
      svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::pack(matrix[i], bytes);
    end
    svtypes_pkg::dyn_array_packer#(bit [9:0], svtypes_pkg::bits_packer#(bit [9:0]))::pack(dyn_bits, bytes);
    svtypes_pkg::dyn_array_packer#(bit signed [8:0], svtypes_pkg::bits_packer#(bit signed [8:0]))::pack(dyn_signed, bytes);
    svtypes_pkg::queue_packer#(Color, svtypes_pkg::bits_packer#(Color))::pack(q_colors, bytes);
    svtypes_pkg::assoc_array_packer#(string, int, svtypes_pkg::string_packer, svtypes_pkg::int_packer)::pack(assoc, bytes);
    svtypes_pkg::queue_packer#(Inner, svtypes_pkg::object_packer#(Inner))::pack(q_inner, bytes);
    svtypes_pkg::fixed_array_packer#(Inner, 2, svtypes_pkg::object_packer#(Inner))::pack(inner_arr, bytes);
    svtypes_pkg::object_packer#(Inner)::pack(nested, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing ManyTypesTx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing ManyTypesTx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("ManyTypesTx", 23, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::bits_packer#(bit [0:0])::unpack(u1, bytes, offset);
    svtypes_pkg::bits_packer#(bit [6:0])::unpack(u7, bytes, offset);
    svtypes_pkg::bits_packer#(bit [8:0])::unpack(u9, bytes, offset);
    svtypes_pkg::bits_packer#(bit [32:0])::unpack(u33, bytes, offset);
    svtypes_pkg::bits_packer#(bit [64:0])::unpack(u65, bytes, offset);
    svtypes_pkg::bits_packer#(bit signed [4:0])::unpack(s5, bytes, offset);
    svtypes_pkg::bits_packer#(bit signed [11:0])::unpack(s12, bytes, offset);
    svtypes_pkg::int_packer::unpack(i32, bytes, offset);
    svtypes_pkg::longint_packer::unpack(i64, bytes, offset);
    svtypes_pkg::bits_packer#(Color)::unpack(color, bytes, offset);
    svtypes_pkg::string_packer::unpack(text, bytes, offset);
    svtypes_pkg::real_packer::unpack(fp64, bytes, offset);
    svtypes_pkg::shortreal_packer::unpack(fp32, bytes, offset);
    svtypes_pkg::fixed_array_packer#(bit [2:0], 4, svtypes_pkg::bits_packer#(bit [2:0]))::unpack(fixed_bits, bytes, offset);
    svtypes_pkg::fixed_array_packer#(bit signed [5:0], 3, svtypes_pkg::bits_packer#(bit signed [5:0]))::unpack(fixed_signed, bytes, offset);
    foreach (matrix[i]) begin
      svtypes_pkg::fixed_array_packer#(int, 2, svtypes_pkg::int_packer)::unpack(matrix[i], bytes, offset);
    end
    svtypes_pkg::dyn_array_packer#(bit [9:0], svtypes_pkg::bits_packer#(bit [9:0]))::unpack(dyn_bits, bytes, offset);
    svtypes_pkg::dyn_array_packer#(bit signed [8:0], svtypes_pkg::bits_packer#(bit signed [8:0]))::unpack(dyn_signed, bytes, offset);
    svtypes_pkg::queue_packer#(Color, svtypes_pkg::bits_packer#(Color))::unpack(q_colors, bytes, offset);
    svtypes_pkg::assoc_array_packer#(string, int, svtypes_pkg::string_packer, svtypes_pkg::int_packer)::unpack(assoc, bytes, offset);
    svtypes_pkg::queue_packer#(Inner, svtypes_pkg::object_packer#(Inner))::unpack(q_inner, bytes, offset);
    svtypes_pkg::fixed_array_packer#(Inner, 2, svtypes_pkg::object_packer#(Inner))::unpack(inner_arr, bytes, offset);
    svtypes_pkg::object_packer#(Inner)::unpack(nested, bytes, offset);
  endfunction
endclass

class ParamTx #(parameter int MODE = 32'd7) extends svtypes_pkg::sv_object;
  int id;
  bit [11:0] payload [3];
  Inner nested;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("ParamTx", 3, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(id, bytes);
    svtypes_pkg::fixed_array_packer#(bit [11:0], 3, svtypes_pkg::bits_packer#(bit [11:0]))::pack(payload, bytes);
    svtypes_pkg::object_packer#(Inner)::pack(nested, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing ParamTx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing ParamTx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("ParamTx", 3, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(id, bytes, offset);
    svtypes_pkg::fixed_array_packer#(bit [11:0], 3, svtypes_pkg::bits_packer#(bit [11:0]))::unpack(payload, bytes, offset);
    svtypes_pkg::object_packer#(Inner)::unpack(nested, bytes, offset);
  endfunction
endclass

class ParamTx_MODE_5 extends ParamTx#(.MODE(32'd5));

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("ParamTx_MODE_5", 3, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(id, bytes);
    svtypes_pkg::fixed_array_packer#(bit [11:0], 3, svtypes_pkg::bits_packer#(bit [11:0]))::pack(payload, bytes);
    svtypes_pkg::object_packer#(Inner)::pack(nested, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing ParamTx_MODE_5 object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing ParamTx_MODE_5 object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("ParamTx_MODE_5", 3, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(id, bytes, offset);
    svtypes_pkg::fixed_array_packer#(bit [11:0], 3, svtypes_pkg::bits_packer#(bit [11:0]))::unpack(payload, bytes, offset);
    svtypes_pkg::object_packer#(Inner)::unpack(nested, bytes, offset);
  endfunction
endclass

class ParamMemberTx extends svtypes_pkg::sv_object;
  int tag;
  ParamTx#(.MODE(32'd5)) param_item;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("ParamMemberTx", 2, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(tag, bytes);
    svtypes_pkg::object_packer#(ParamTx#(.MODE(32'd5)))::pack(param_item, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing ParamMemberTx object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing ParamMemberTx object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("ParamMemberTx", 2, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(tag, bytes, offset);
    svtypes_pkg::object_packer#(ParamTx#(.MODE(32'd5)))::unpack(param_item, bytes, offset);
  endfunction
endclass
