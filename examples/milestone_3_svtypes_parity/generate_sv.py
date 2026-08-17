from examples.milestone_3_svtypes_parity.tests.types import (
    BaseTx,
    Color,
    Inner,
    LargeMixedTx,
    M3Tx,
    ManyTypesTx,
    ParamMemberTx,
    ParamTx,
    ParamTx_MODE_5,
)


def main() -> None:
    print("// Generated from examples.milestone_3_svtypes_parity.tests.types")
    print("// Do not edit by hand.")
    print()
    print(Color.to_sv_enum())
    print()
    print(Inner.to_sv_obj())
    print()
    print(BaseTx.to_sv_obj())
    print()
    print(M3Tx.to_sv_obj())
    print()
    print(LargeMixedTx.to_sv_obj())
    print()
    print(ManyTypesTx.to_sv_obj())
    print()
    print(ParamTx.to_sv_obj())
    print()
    print(ParamTx_MODE_5.to_sv_obj())
    print()
    print(ParamMemberTx.to_sv_obj())


if __name__ == "__main__":
    main()
