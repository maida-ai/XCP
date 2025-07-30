@0xbf514fc46d4d410b;

struct FrameHeader {
  channelId  @0 :UInt32;               # QUIC/HTTP2 stream id
  msgType    @1 :UInt16;               # 0x0000‥0x00FF CONTROL, 0x0100 DATA
  bodyCodec  @2 :UInt16;               # see §5.1
  schemaKey  @3 :SchemaKey;            # identifies Ether schema (for DATA)
  msgId      @4 :UInt64;               # for ACK/NACK, dedup
  inReplyTo  @5 :UInt64;               # correlation id (0 if none)
  tags       @6 :List(Tag);            # free-form key/value
}

struct SchemaKey {
  nsHash   @0 :UInt32;                 # FNV-1a namespace hash
  kindId   @1 :UInt32;                 # FNV-1a of kind string
  major    @2 :UInt16;                 # breaking version
  minor    @3 :UInt16;                 # additive version
  hash128  @4 :Data(16);               # first 128 bits SHA-256 canonical schema JSON
}

struct Tag {
  key @0 :Text;
  val @1 :Text;
}
