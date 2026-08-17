`include "svtypes_pkg.sv"
`include "sv/svx_pkg.sv"
`include "examples/milestone_3_svtypes_parity/generated_types.sv"

module tb;
  timeunit 1ns;
  timeprecision 1ps;

  import svx_pkg::*;

  initial begin
    chandle payload;
    byte unsigned bytes[$];
    int offset;
    M3Tx tx;
    ManyTypesTx many_tx;
    ParamTx_MODE_5 param_tx;
    ParamMemberTx param_member_tx;
    Inner inner;

    $display("M3 SvTypes generated parity test start @ %0.3f ns", $realtime);

    svx_init();
    svx_load("examples.milestone_3_svtypes_parity.tests.parity_test");

    svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_puts_m3_tx");

    svx_channel_get_payload("m3.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected kind");
    if (svx_payload_type_name(payload) != "M3Tx") $fatal(2, "unexpected type");
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    tx = new();
    offset = 0;
    tx.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "unpack offset=%0d size=%0d", offset, bytes.size());
    if (tx.id != -3) $fatal(2, "bad id %0d", tx.id);
    if (tx.data[0] != 10 || tx.data[1] != -20) $fatal(2, "bad fixed array");
    if (tx.label != "M3") $fatal(2, "bad label %s", tx.label);
    if (tx.addr != 16'hbeef) $fatal(2, "bad addr 0x%04h", tx.addr);
    if (tx.serial != -64'sh0102030405060708) $fatal(2, "bad serial 0x%016h", tx.serial);
    if (tx.color != BLUE) $fatal(2, "bad color");
    if (tx.ratio < 3.249999 || tx.ratio > 3.250001) $fatal(2, "bad ratio %f", tx.ratio);
    if (tx.temp < -1.5001 || tx.temp > -1.4999) $fatal(2, "bad temp %f", tx.temp);
    if (tx.dyn.size() != 3 || tx.dyn[0] != 7 || tx.dyn[1] != 8 || tx.dyn[2] != -9) begin
      $fatal(2, "bad dyn");
    end
    if (tx.q.size() != 2 || tx.q[0] != 11 || tx.q[1] != -12) $fatal(2, "bad queue");
    if (tx.inner.a != 42 || tx.inner.b != 8'haa) $fatal(2, "bad inner");
    $display("SV unpacked generated M3 tx label=%s size=%0d", tx.label, bytes.size());

    svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_puts_many_types_tx");

    svx_channel_get_payload("m3.many.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected many kind");
    if (svx_payload_type_name(payload) != "ManyTypesTx") $fatal(2, "unexpected many type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    many_tx = new();
    offset = 0;
    many_tx.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "many unpack offset=%0d size=%0d", offset, bytes.size());
    if (many_tx.u1 != 1'b1) $fatal(2, "bad u1");
    if (many_tx.u7 != 7'h7e) $fatal(2, "bad u7");
    if (many_tx.u9 != 9'h1ab) $fatal(2, "bad u9");
    if (many_tx.u33 != 33'h123456789) $fatal(2, "bad u33");
    if (many_tx.u65 != 65'h10123456789abcdef) $fatal(2, "bad u65");
    if (many_tx.s5 != -5'sd7) $fatal(2, "bad s5");
    if (many_tx.s12 != -12'sd1023) $fatal(2, "bad s12");
    if (many_tx.i32 != -32'sh01234567) $fatal(2, "bad i32");
    if (many_tx.i64 != -64'sh0102030405060708) $fatal(2, "bad i64");
    if (many_tx.color != GREEN) $fatal(2, "bad many color");
    if (many_tx.text != "many-types") $fatal(2, "bad many text");
    if (many_tx.fp64 < -3.141593 || many_tx.fp64 > -3.141592) $fatal(2, "bad fp64 %f", many_tx.fp64);
    if (many_tx.fp32 < 12.4999 || many_tx.fp32 > 12.5001) $fatal(2, "bad fp32 %f", many_tx.fp32);
    if (many_tx.fixed_bits[0] != 0 || many_tx.fixed_bits[1] != 1 ||
        many_tx.fixed_bits[2] != 6 || many_tx.fixed_bits[3] != 7) begin
      $fatal(2, "bad fixed_bits");
    end
    if (many_tx.fixed_signed[0] != -1 || many_tx.fixed_signed[1] != -17 ||
        many_tx.fixed_signed[2] != 15) begin
      $fatal(2, "bad fixed_signed");
    end
    if (many_tx.matrix[0][0] != 1 || many_tx.matrix[0][1] != -2 ||
        many_tx.matrix[1][0] != 3 || many_tx.matrix[1][1] != -4) begin
      $fatal(2, "bad matrix");
    end
    if (many_tx.dyn_bits.size() != 3 || many_tx.dyn_bits[0] != 0 ||
        many_tx.dyn_bits[1] != 10'h155 || many_tx.dyn_bits[2] != 10'h3ff) begin
      $fatal(2, "bad dyn_bits");
    end
    if (many_tx.dyn_signed.size() != 3 || many_tx.dyn_signed[0] != -1 ||
        many_tx.dyn_signed[1] != -9'sd100 || many_tx.dyn_signed[2] != 127) begin
      $fatal(2, "bad dyn_signed");
    end
    if (many_tx.q_colors.size() != 3 || many_tx.q_colors[0] != RED ||
        many_tx.q_colors[1] != BLUE || many_tx.q_colors[2] != GREEN) begin
      $fatal(2, "bad q_colors");
    end
    if (many_tx.assoc.num() != 2 || !many_tx.assoc.exists("apple") ||
        !many_tx.assoc.exists("banana") || many_tx.assoc["apple"] != 11 ||
        many_tx.assoc["banana"] != -22) begin
      $fatal(2, "bad assoc");
    end
    if (many_tx.q_inner.size() != 2 || many_tx.q_inner[0].a != 10 ||
        many_tx.q_inner[0].b != 8'h11 || many_tx.q_inner[1].a != -20 ||
        many_tx.q_inner[1].b != 8'h22) begin
      $fatal(2, "bad q_inner");
    end
    if (many_tx.inner_arr[0].a != 30 || many_tx.inner_arr[0].b != 8'h33 ||
        many_tx.inner_arr[1].a != -40 || many_tx.inner_arr[1].b != 8'h44) begin
      $fatal(2, "bad inner_arr");
    end
    if (many_tx.nested.a != 50 || many_tx.nested.b != 8'h55) $fatal(2, "bad nested");
    $display("SV unpacked many-types tx size=%0d", bytes.size());

    fork
      begin
        svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_gets_m3_tx");
      end
      begin
        #(1.25ns);
        tx = new();
        tx.id = 100;
        tx.data[0] = 1;
        tx.data[1] = 2;
        tx.label = "SV";
        tx.addr = 16'h1234;
        tx.serial = 64'h0102030405060708;
        tx.color = GREEN;
        tx.ratio = -2.5;
        tx.temp = 6.25;
        tx.dyn = new[2];
        tx.dyn[0] = 3;
        tx.dyn[1] = 4;
        tx.q.delete();
        tx.q.push_back(5);
        tx.q.push_back(6);
        tx.q.push_back(7);
        tx.inner = new();
        tx.inner.a = -8;
        tx.inner.b = 8'h55;
        bytes.delete();
        tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m3.sv_to_py",
          bytes,
          "svtypes",
          "M3Tx",
          "application/x-svtypes"
        );
      end
    join

    fork
      begin
        svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_gets_many_types_tx");
      end
      begin
        #(1.25ns);
        many_tx = new();
        many_tx.u1 = 1'b0;
        many_tx.u7 = 7'h55;
        many_tx.u9 = 9'h12a;
        many_tx.u33 = 33'h111111111;
        many_tx.u65 = 65'h1fedcba9876543210;
        many_tx.s5 = -5'sd8;
        many_tx.s12 = 12'sd1023;
        many_tx.i32 = 32'h10203040;
        many_tx.i64 = 64'h0102030405060708;
        many_tx.color = BLUE;
        many_tx.text = "sv-many-types";
        many_tx.fp64 = 2.75;
        many_tx.fp32 = -3.5;
        many_tx.fixed_bits[0] = 7;
        many_tx.fixed_bits[1] = 6;
        many_tx.fixed_bits[2] = 1;
        many_tx.fixed_bits[3] = 0;
        many_tx.fixed_signed[0] = -16;
        many_tx.fixed_signed[1] = 0;
        many_tx.fixed_signed[2] = 15;
        many_tx.matrix[0][0] = 9;
        many_tx.matrix[0][1] = 8;
        many_tx.matrix[1][0] = 7;
        many_tx.matrix[1][1] = 6;
        many_tx.dyn_bits = new[3];
        many_tx.dyn_bits[0] = 1;
        many_tx.dyn_bits[1] = 2;
        many_tx.dyn_bits[2] = 10'h3fe;
        many_tx.dyn_signed = new[4];
        many_tx.dyn_signed[0] = -128;
        many_tx.dyn_signed[1] = -1;
        many_tx.dyn_signed[2] = 0;
        many_tx.dyn_signed[3] = 127;
        many_tx.q_colors.delete();
        many_tx.q_colors.push_back(GREEN);
        many_tx.q_colors.push_back(RED);
        many_tx.assoc.delete();
        many_tx.assoc["cat"] = 31;
        many_tx.assoc["dog"] = -41;
        many_tx.q_inner.delete();
        inner = new();
        inner.a = 101;
        inner.b = 8'ha1;
        many_tx.q_inner.push_back(inner);
        inner = new();
        inner.a = -102;
        inner.b = 8'ha2;
        many_tx.q_inner.push_back(inner);
        many_tx.inner_arr[0] = new();
        many_tx.inner_arr[0].a = 201;
        many_tx.inner_arr[0].b = 8'hb1;
        many_tx.inner_arr[1] = new();
        many_tx.inner_arr[1].a = -202;
        many_tx.inner_arr[1].b = 8'hb2;
        many_tx.nested = new();
        many_tx.nested.a = 303;
        many_tx.nested.b = 8'hc3;
        bytes.delete();
        many_tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m3.many.sv_to_py",
          bytes,
          "svtypes",
          "ManyTypesTx",
          "application/x-svtypes"
        );
      end
    join

    svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_puts_param_tx");

    svx_channel_get_payload("m3.param.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected param kind");
    if (svx_payload_type_name(payload) != "ParamTx_MODE_5") $fatal(2, "unexpected param type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    param_tx = new();
    offset = 0;
    param_tx.unpack(bytes, offset);
    if (offset != bytes.size()) $fatal(2, "param unpack offset=%0d size=%0d", offset, bytes.size());
    if (param_tx.MODE != 5) $fatal(2, "bad param MODE %0d", param_tx.MODE);
    if (param_tx.id != 77) $fatal(2, "bad param id %0d", param_tx.id);
    if (param_tx.payload[0] != 12'h001 || param_tx.payload[1] != 12'habc ||
        param_tx.payload[2] != 12'hfff) begin
      $fatal(2, "bad param payload");
    end
    if (param_tx.nested.a != -17 || param_tx.nested.b != 8'h5a) $fatal(2, "bad param nested");
    $display("SV unpacked parameterized tx mode=%0d size=%0d", param_tx.MODE, bytes.size());

    fork
      begin
        svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_gets_param_tx");
      end
      begin
        #(1.25ns);
        param_tx = new();
        param_tx.id = 88;
        param_tx.payload[0] = 12'h010;
        param_tx.payload[1] = 12'h020;
        param_tx.payload[2] = 12'h030;
        param_tx.nested = new();
        param_tx.nested.a = 123;
        param_tx.nested.b = 8'hc4;
        bytes.delete();
        param_tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m3.param.sv_to_py",
          bytes,
          "svtypes",
          "ParamTx_MODE_5",
          "application/x-svtypes"
        );
      end
    join

    svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_puts_param_member_tx");

    svx_channel_get_payload("m3.param_member.py_to_sv", payload);
    if (svx_payload_kind(payload) != "svtypes") $fatal(2, "unexpected param member kind");
    if (svx_payload_type_name(payload) != "ParamMemberTx") $fatal(2, "unexpected param member type");
    bytes.delete();
    svx_payload_to_byte_queue(payload, bytes);
    svx_payload_destroy(payload);

    param_member_tx = new();
    offset = 0;
    param_member_tx.unpack(bytes, offset);
    if (offset != bytes.size()) begin
      $fatal(2, "param member unpack offset=%0d size=%0d", offset, bytes.size());
    end
    if (param_member_tx.tag != 501) $fatal(2, "bad param member tag %0d", param_member_tx.tag);
    if (param_member_tx.param_item.MODE != 5) begin
      $fatal(2, "bad param member MODE %0d", param_member_tx.param_item.MODE);
    end
    if (param_member_tx.param_item.id != 502) begin
      $fatal(2, "bad param member id %0d", param_member_tx.param_item.id);
    end
    if (param_member_tx.param_item.payload[0] != 12'h101 ||
        param_member_tx.param_item.payload[1] != 12'h202 ||
        param_member_tx.param_item.payload[2] != 12'h303) begin
      $fatal(2, "bad param member payload");
    end
    if (param_member_tx.param_item.nested.a != 503 ||
        param_member_tx.param_item.nested.b != 8'hd5) begin
      $fatal(2, "bad param member nested");
    end
    $display("SV unpacked parameterized member mode=%0d size=%0d",
             param_member_tx.param_item.MODE, bytes.size());

    fork
      begin
        svx_start("examples.milestone_3_svtypes_parity.tests.parity_test.python_gets_param_member_tx");
      end
      begin
        #(1.25ns);
        param_member_tx = new();
        param_member_tx.tag = 601;
        param_member_tx.param_item = new();
        param_member_tx.param_item.id = 602;
        param_member_tx.param_item.payload[0] = 12'h111;
        param_member_tx.param_item.payload[1] = 12'h222;
        param_member_tx.param_item.payload[2] = 12'h333;
        param_member_tx.param_item.nested = new();
        param_member_tx.param_item.nested.a = 603;
        param_member_tx.param_item.nested.b = 8'he6;
        bytes.delete();
        param_member_tx.pack(bytes);
        svx_channel_put_byte_queue(
          "m3.param_member.sv_to_py",
          bytes,
          "svtypes",
          "ParamMemberTx",
          "application/x-svtypes"
        );
      end
    join

    $display("M3 SvTypes generated parity test done @ %0.3f ns", $realtime);
    $finish;
  end
endmodule
