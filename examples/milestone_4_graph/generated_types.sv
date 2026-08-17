// Generated from examples.milestone_4_graph.tests.types
// Do not edit by hand.

typedef class GraphNode;
typedef class GraphPair;
typedef class GraphQueue;
typedef class GraphRefQueue;

class GraphNode extends svtypes_pkg::sv_object;
  int data;
  GraphNode next;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("GraphNode", 2, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(data, bytes);
    svtypes_pkg::object_packer#(GraphNode)::pack(next, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing GraphNode object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing GraphNode object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("GraphNode", 2, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(data, bytes, offset);
    svtypes_pkg::object_packer#(GraphNode)::unpack(next, bytes, offset);
  endfunction
endclass

class GraphPair extends svtypes_pkg::sv_object;
  int tag;
  GraphNode left;
  GraphNode right;

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("GraphPair", 3, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(tag, bytes);
    svtypes_pkg::object_packer#(GraphNode)::pack(left, bytes);
    svtypes_pkg::object_packer#(GraphNode)::pack(right, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing GraphPair object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing GraphPair object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("GraphPair", 3, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(tag, bytes, offset);
    svtypes_pkg::object_packer#(GraphNode)::unpack(left, bytes, offset);
    svtypes_pkg::object_packer#(GraphNode)::unpack(right, bytes, offset);
  endfunction
endclass

class GraphQueue extends svtypes_pkg::sv_object;
  int tag;
  GraphNode nodes [$];

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("GraphQueue", 2, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(tag, bytes);
    svtypes_pkg::queue_packer#(GraphNode, svtypes_pkg::object_packer#(GraphNode))::pack(nodes, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing GraphQueue object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing GraphQueue object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("GraphQueue", 2, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(tag, bytes, offset);
    svtypes_pkg::queue_packer#(GraphNode, svtypes_pkg::object_packer#(GraphNode))::unpack(nodes, bytes, offset);
  endfunction
endclass

class GraphRefQueue extends svtypes_pkg::sv_object;
  int tag;
  GraphNode nodes [$];

  virtual function void pack(ref byte unsigned bytes[$]);
    svtypes_pkg::begin_pack_graph();
    svtypes_pkg::pack_object_value(this, bytes);
  endfunction

  virtual function void pack_body(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("GraphRefQueue", 2, __svtypes_object_number, bytes);
    svtypes_pkg::int_packer::pack(tag, bytes);
    svtypes_pkg::queue_packer#(GraphNode, svtypes_pkg::object_packer#(GraphNode))::pack(nodes, bytes);
  endfunction

  virtual function void unpack(ref byte unsigned bytes[$], ref int offset);
    byte unsigned present;
    svtypes_pkg::require_available(bytes, offset, 1, "object presence");
    present = bytes[offset];
    offset += 1;
    if (present == 8'h02) begin
      $fatal(2, "SvTypes cannot unpack root reference into existing GraphRefQueue object");
    end
    if (present != 8'h01) begin
      $fatal(2, "SvTypes cannot unpack null into existing GraphRefQueue object");
    end
    unpack_body(bytes, offset);
  endfunction

  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    longint unsigned incoming_svtypes_object_number;
    svtypes_pkg::unpack_object_header("GraphRefQueue", 2, incoming_svtypes_object_number, bytes, offset);
    __svtypes_object_number = incoming_svtypes_object_number;
    svtypes_pkg::register_object(this);
    svtypes_pkg::int_packer::unpack(tag, bytes, offset);
    svtypes_pkg::queue_packer#(GraphNode, svtypes_pkg::object_packer#(GraphNode))::unpack(nodes, bytes, offset);
  endfunction
endclass
