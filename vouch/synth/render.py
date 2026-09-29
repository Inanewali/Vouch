"""Render invoices to PDF in several deliberately different layouts.

Real invoices disagree on almost everything: where the vendor name sits, what the
total is called, date formats, whether a previous balance is shown. Each layout
here reproduces one of those habits so extraction has to cope with them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas


@dataclass
class Line:
    description: str
    qty: int
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass
class Invoice:
    doc_id: str
    vendor_name: str            # as printed on the invoice
    vendor_address: str
    invoice_number: str
    invoice_date: date
    lines: list[Line]
    tax_rate: float
    tax_label: str
    currency: str
    layout: str
    customer: str = "Maple Ridge Manufacturing Inc."
    previous_balance: float = 0.0   # shown on some statements-style invoices; NOT part of the total
    po_number: str | None = None

    @property
    def subtotal(self) -> float:
        return round(sum(l.amount for l in self.lines), 2)

    @property
    def tax(self) -> float:
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax, 2)


def money(x: float) -> str:
    return f"{x:,.2f}"


W, H = LETTER


def _lines_table(c: canvas.Canvas, inv: Invoice, y: float, cols=(54, 330, 400, 480),
                 headers=("Description", "Qty", "Unit price", "Amount")) -> float:
    c.setFont("Helvetica-Bold", 9)
    for x, h in zip(cols, headers):
        c.drawString(x, y, h)
    y -= 4
    c.line(54, y, W - 54, y)
    y -= 14
    c.setFont("Helvetica", 9)
    for ln in inv.lines:
        c.drawString(cols[0], y, ln.description[:52])
        c.drawRightString(cols[1] + 30, y, str(ln.qty))
        c.drawRightString(cols[2] + 55, y, money(ln.unit_price))
        c.drawRightString(W - 54, y, money(ln.amount))
        y -= 14
    return y


def layout_classic(c: canvas.Canvas, inv: Invoice) -> None:
    """Vendor letterhead top-left, 'Invoice No.' / 'Invoice Date' block top-right, 'TOTAL'."""
    c.setFont("Helvetica-Bold", 18)
    c.drawString(54, H - 72, inv.vendor_name)
    c.setFont("Helvetica", 9)
    for i, part in enumerate(inv.vendor_address.split("\n")):
        c.drawString(54, H - 90 - 12 * i, part)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - 54, H - 72, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 54, H - 96, f"Invoice No.: {inv.invoice_number}")
    c.drawRightString(W - 54, H - 110, f"Invoice Date: {inv.invoice_date:%B %d, %Y}")
    if inv.po_number:
        c.drawRightString(W - 54, H - 124, f"PO: {inv.po_number}")
    c.drawString(54, H - 160, "Bill to:")
    c.drawString(54, H - 174, inv.customer)
    y = _lines_table(c, inv, H - 220)
    y -= 10
    c.drawRightString(W - 140, y, "Subtotal")
    c.drawRightString(W - 54, y, money(inv.subtotal))
    c.drawRightString(W - 140, y - 14, f"{inv.tax_label} ({inv.tax_rate:.0%})")
    c.drawRightString(W - 54, y - 14, money(inv.tax))
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - 140, y - 32, f"TOTAL {inv.currency}")
    c.drawRightString(W - 54, y - 32, money(inv.total))


def layout_statement(c: canvas.Canvas, inv: Invoice) -> None:
    """Statement style: carries a previous balance, labels the grand figure 'Amount Due'.
    The invoice's own total is shown as 'Current charges'."""
    c.setFont("Helvetica", 9)
    c.drawString(54, H - 60, "Remit to:")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(54, H - 78, inv.vendor_name.upper())
    c.setFont("Helvetica", 9)
    c.drawString(54, H - 92, inv.vendor_address.replace("\n", ", "))
    c.setFont("Helvetica", 10)
    c.drawString(360, H - 140, f"Bill #  {inv.invoice_number}")
    c.drawString(360, H - 154, f"Date  {inv.invoice_date:%Y-%m-%d}")
    c.drawString(360, H - 168, f"Account  {inv.customer[:18]}")
    y = _lines_table(c, inv, H - 210, headers=("Item", "Units", "Rate", "Line total"))
    y -= 10
    rows = [("Net amount", inv.subtotal), (f"{inv.tax_label}", inv.tax),
            ("Current charges", inv.total), ("Previous balance", inv.previous_balance),
            ("Amount Due", inv.total + inv.previous_balance)]
    for i, (label, val) in enumerate(rows):
        c.setFont("Helvetica-Bold" if label == "Amount Due" else "Helvetica", 10)
        c.drawString(360, y - 15 * i, label)
        c.drawRightString(W - 54, y - 15 * i, f"{inv.currency} {money(val)}")


def layout_compact(c: canvas.Canvas, inv: Invoice) -> None:
    """Small-business template: vendor name top-right, DD/MM/YYYY dates, 'Balance' as total."""
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 54, H - 64, inv.vendor_name)
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 54, H - 78, inv.vendor_address.replace("\n", " | "))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(54, H - 64, "Tax Invoice")
    c.setFont("Helvetica", 10)
    c.drawString(54, H - 110, f"Ref: {inv.invoice_number}")
    c.drawString(54, H - 124, f"Issued (DD/MM/YYYY): {inv.invoice_date:%d/%m/%Y}")
    c.drawString(54, H - 138, f"Customer: {inv.customer}")
    y = _lines_table(c, inv, H - 180)
    y -= 12
    c.drawString(330, y, f"Sub-total: {money(inv.subtotal)}")
    c.drawString(330, y - 14, f"{inv.tax_label}: {money(inv.tax)}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(330, y - 32, f"Balance ({inv.currency}): {money(inv.total)}")


LAYOUTS = {"classic": layout_classic, "statement": layout_statement, "compact": layout_compact}


def render(inv: Invoice, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setTitle(f"{inv.vendor_name} {inv.invoice_number}")
    LAYOUTS[inv.layout](c, inv)
    c.setFont("Helvetica", 7)
    c.drawString(54, 40, "SYNTHETIC DOCUMENT - generated for the Vouch benchmark. Not a real invoice.")
    c.showPage()
    c.save()
