"""Reconstruct the ASUS GX10 DMI branch in dgx_ec_fan_control.c.

Recovered from the shipped binary's rodata strings ("ASUSTeK COMPUTER INC.",
"GX10", "asus_gx10") plus the existing nvidia/lenovo branches. The original
patched source was not preserved; this restores a reproducible build input.
"""
import shutil
import subprocess
import sys

SRC = "/home/toor/spark-energy/drivers/dgx-spark-fan-control/kernel/dgx_ec_fan_control.c"

OLD = '''	nvidia_spark = dmi_match(DMI_SYS_VENDOR, "NVIDIA") &&
		       dmi_match(DMI_PRODUCT_NAME, "NVIDIA_DGX_Spark") &&
		       dmi_match(DMI_BOARD_NAME, "P4242");
	/* Lenovo reports the board name as INVALID on the qualified 30KL SKU. */
	lenovo_pgx = dmi_match(DMI_SYS_VENDOR, "LENOVO") &&
		     dmi_match(DMI_PRODUCT_NAME, "30KL0005GF");

	return nvidia_spark || lenovo_pgx;'''

NEW = '''	nvidia_spark = dmi_match(DMI_SYS_VENDOR, "NVIDIA") &&
		       dmi_match(DMI_PRODUCT_NAME, "NVIDIA_DGX_Spark") &&
		       dmi_match(DMI_BOARD_NAME, "P4242");
	/* Lenovo reports the board name as INVALID on the qualified 30KL SKU. */
	lenovo_pgx = dmi_match(DMI_SYS_VENDOR, "LENOVO") &&
		     dmi_match(DMI_PRODUCT_NAME, "30KL0005GF");
	/* ASUS GX10 (GB10): vendor "ASUSTeK COMPUTER INC.", product/board "GX10".
	 * The board name is not required: GX10 reports it equal to the product. */
	asus_gx10 = dmi_match(DMI_SYS_VENDOR, "ASUSTeK COMPUTER INC.") &&
		    dmi_match(DMI_PRODUCT_NAME, "GX10");

	return nvidia_spark || lenovo_pgx || asus_gx10;'''

DECLS_OLD = "\tbool nvidia_spark;\n\tbool lenovo_pgx;\n"
DECLS_NEW = "\tbool nvidia_spark;\n\tbool lenovo_pgx;\n\tbool asus_gx10;\n"

with open(SRC, encoding="utf-8") as fh:
    text = fh.read()

if "asus_gx10" in text:
    print("already patched")
    sys.exit(0)

if OLD not in text:
    print("ERROR: anchor not found; source differs from expectation")
    sys.exit(1)
text = text.replace(OLD, NEW)
text = text.replace(DECLS_OLD, DECLS_NEW, 1)
with open(SRC, "w", encoding="utf-8") as fh:
    fh.write(text)
print("patch applied")
