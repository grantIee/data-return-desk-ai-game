from __future__ import annotations

import os
import random
import json
from datetime import datetime, timedelta
from pathlib import Path

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from PIL import Image, ImageDraw

from models import Customer, Decision, FraudAssessment, SpendingPotential
from rules import evaluate

GENERATED_DIR = Path(__file__).parent / "generated"

FIRST_NAMES = [
    "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Quinn", "Avery", "Blake",
    "Cameron", "Dakota", "Emery", "Finley", "Harper", "Jamie", "Kendall", "Logan",
    "Madison", "Noel", "Parker", "Reagan", "Sage", "Tatum", "Val", "Whitney",
    "Alex", "Drew", "Ellis", "Francis", "Gray", "Hayden", "Indigo", "Jesse",
    "Kerry", "Lane", "Marley", "Nico", "Oakley", "Peyton", "Reese", "Shea",
    "Tracy", "Umber", "Vivian", "Wren", "Yael", "Zion", "Arden", "Briar",
    "Charlie", "Devon",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
    "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera",
    "Campbell", "Mitchell", "Carter", "Roberts",
]

ITEMS = [
    ("Wireless Headphones", 89.99), ("Bluetooth Speaker", 49.99), ("USB-C Hub", 34.99),
    ("Mechanical Keyboard", 129.99), ("Gaming Mouse", 59.99), ("Webcam HD", 79.99),
    ("Monitor Stand", 44.99), ("Desk Lamp", 29.99), ("Phone Case", 19.99),
    ("Screen Protector", 12.99), ("Laptop Sleeve", 24.99), ("Wireless Charger", 39.99),
    ("Power Bank", 54.99), ("HDMI Cable", 14.99), ("Mouse Pad", 9.99),
    ("USB Drive 64GB", 16.99), ("Ethernet Adapter", 22.99), ("Cable Organizer", 11.99),
    ("Tablet Stand", 27.99), ("Smart Plug", 19.99), ("Ring Light", 34.99),
    ("Noise Machine", 44.99), ("Portable SSD", 89.99), ("Surge Protector", 24.99),
    ("Desk Organizer", 32.99), ("Wrist Rest", 18.99), ("Air Duster", 8.99),
    ("Privacy Screen", 39.99), ("Laptop Stand", 49.99), ("Docking Station", 149.99),
]

STORE_NAMES = [
    "TechMart Electronics", "Digital World", "ByteShop", "Circuit City Express",
    "The Gadget Store", "ElectroHub", "SmartBuy Tech", "NexGen Electronics",
]

FILLER_SLIDE_TITLES = [
    "Q3 Fraud Summary", "Regional Trends Overview", "Methodology & Data Sources",
    "Key Performance Indicators", "Year-over-Year Comparison", "Risk Assessment Framework",
    "Departmental Highlights", "Compliance Updates", "Next Steps & Action Items",
    "Executive Summary", "Quarterly Benchmarks", "Industry Comparison",
]

FILLER_BULLETS = [
    "Overall fraud rates decreased 3.2% quarter-over-quarter",
    "Eastern region saw highest volume of flagged transactions",
    "New ML model deployed with 94.7% precision on test set",
    "Customer satisfaction scores remain above 87th percentile",
    "Average resolution time reduced to 2.3 business days",
    "Cross-department collaboration improved flagging accuracy",
    "Automated screening now handles 67% of initial reviews",
    "False positive rate dropped from 12% to 8.5%",
    "Training sessions completed for 94% of frontline staff",
    "Budget utilization at 91% — within acceptable range",
    "Vendor scoring model updated with latest data feeds",
    "Escalation paths streamlined per policy revision 4.2",
    "Mobile channel fraud attempts up 15% — monitoring closely",
    "Synthetic identity detection improved by 22%",
    "Regulatory audit findings: zero critical, two minor",
]


def load_bq_lookup() -> dict[str, dict]:
    """Load the BQ lookup data from the local JSONL file."""
    lookup = {}
    bq_file = Path(__file__).parent / "bq_load.jsonl"
    if bq_file.exists():
        with open(bq_file) as f:
            for line in f:
                row = json.loads(line)
                lookup[row["customer_id"]] = row
    return lookup


# Load BQ data at module level
_bq_lookup = load_bq_lookup()


def generate_customer(index: int) -> Customer:
    """Generate a single customer with controlled randomness."""
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    customer_id = f"CUST-{index:04d}"

    photo_included = random.random() < 0.7  # 70% have photos

    total_purchases = random.randint(20, 100)

    # Vary return rates to create interesting decision boundaries
    if random.random() < 0.3:
        # High return rate customers
        return_rate = random.uniform(0.21, 0.40)
    elif random.random() < 0.5:
        # Borderline customers
        return_rate = random.uniform(0.15, 0.25)
    else:
        # Normal customers
        return_rate = random.uniform(0.02, 0.19)

    total_returns = max(1, int(total_purchases * return_rate))
    actual_return_rate = total_returns / total_purchases

    total_purchase_amount = round(random.uniform(2000, 25000), 2)

    # Vary dollar ratio to create interesting Rule 4 scenarios
    if random.random() < 0.4:
        return_dollar_ratio = random.uniform(0.05, 0.75)  # Under 80%
    else:
        return_dollar_ratio = random.uniform(0.80, 0.95)  # At or above 80%

    total_return_amount = round(total_purchase_amount * return_dollar_ratio, 2)

    # Pick fraud assessment with distribution
    fraud_roll = random.random()
    if fraud_roll < 0.5:
        fraud_assessment = FraudAssessment.NOT_MALICIOUS
    elif fraud_roll < 0.8:
        fraud_assessment = FraudAssessment.SUSPICIOUS
    else:
        fraud_assessment = FraudAssessment.FLAGGED

    # Spending potential
    spend_roll = random.random()
    if spend_roll < 0.35:
        spending_potential = SpendingPotential.HIGH
    elif spend_roll < 0.70:
        spending_potential = SpendingPotential.MEDIUM
    else:
        spending_potential = SpendingPotential.LOW

    item_name, item_price = random.choice(ITEMS)

    # Get BQ lookup data (loyalty tier)
    bq_data = _bq_lookup.get(customer_id, {})
    loyalty_tier = bq_data.get("loyalty_tier", "NONE")

    customer = Customer(
        id=customer_id,
        name=name,
        photo_included=photo_included,
        total_purchases=total_purchases,
        total_returns=total_returns,
        return_rate=round(actual_return_rate, 4),
        total_purchase_amount=total_purchase_amount,
        total_return_amount=total_return_amount,
        return_dollar_ratio=round(return_dollar_ratio, 4),
        current_return_item=item_name,
        current_return_amount=item_price,
        loyalty_tier=loyalty_tier,
        fraud_assessment=fraud_assessment,
        spending_potential=spending_potential,
        correct_decision=Decision.ACCEPT,  # placeholder
    )

    # Compute correct decision
    decision, _ = evaluate(customer)
    customer = customer.model_copy(update={"correct_decision": decision})

    return customer


def _generate_photo(path: Path, name: str) -> None:
    """Generate a simple avatar placeholder image."""
    img = Image.new("RGB", (120, 120), color=(70, 130, 180))
    draw = ImageDraw.Draw(img)
    # Draw initials
    initials = "".join(w[0] for w in name.split()[:2])
    # Center the text approximately
    draw.text((35, 40), initials, fill="white")
    # Draw a circle border
    draw.ellipse([5, 5, 115, 115], outline="white", width=3)
    img.save(path)


def generate_receipt_pdf(customer: Customer, output_dir: Path) -> Path:
    """Generate a receipt PDF for a customer."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    store_name = random.choice(STORE_NAMES)
    date = (datetime.now() - timedelta(days=random.randint(1, 30))).strftime("%B %d, %Y")

    # Header
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 15, store_name, new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "123 Commerce Blvd, Suite 100", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 6, "Tel: (555) 123-4567", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # Divider
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # Return Request header
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "RETURN REQUEST", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(3)

    # Customer info
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(40, 8, "Customer:", new_x="RIGHT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, customer.name, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(40, 8, "Customer ID:", new_x="RIGHT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, customer.id, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(40, 8, "Date:", new_x="RIGHT")
    pdf.cell(0, 8, date, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Photo section
    if customer.photo_included:
        photo_path = output_dir / f"{customer.id}_photo.png"
        _generate_photo(photo_path, customer.name)
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "Customer Photo on File:", new_x="LMARGIN", new_y="NEXT")
        pdf.image(str(photo_path), x=10, y=pdf.get_y(), w=30, h=30)
        pdf.ln(35)
        # Clean up temp photo
        photo_path.unlink(missing_ok=True)

    # Divider
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # Item details
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Item Details", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(50, 8, "Item:", new_x="RIGHT")
    pdf.cell(0, 8, customer.current_return_item, new_x="LMARGIN", new_y="NEXT")

    pdf.cell(50, 8, "Amount:", new_x="RIGHT")
    pdf.cell(0, 8, f"${customer.current_return_amount:.2f}", new_x="LMARGIN", new_y="NEXT")

    pdf.cell(50, 8, "Reason:", new_x="RIGHT")
    reasons = ["Defective", "Wrong item received", "Changed mind", "Better price found", "Not as described"]
    pdf.cell(0, 8, random.choice(reasons), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(10)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # Footer
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 6, "This receipt is for return processing purposes only.", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 6, f"Transaction Ref: RTN-{random.randint(100000, 999999)}", new_x="LMARGIN", new_y="NEXT", align="C")

    output_path = output_dir / "receipt.pdf"
    pdf.output(str(output_path))
    return output_path


def generate_transactions_xlsx(customer: Customer, output_dir: Path) -> Path:
    """Generate transaction history Excel file."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Transaction History"

    # Styles
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Title
    ws.merge_cells("A1:E1")
    ws["A1"] = f"Transaction History — {customer.name} ({customer.id})"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")

    # Headers
    headers = ["Date", "Item", "Amount", "Type", "Reference"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center")

    # Generate transactions that match the customer's profile
    num_transactions = random.randint(30, 80)

    # Calculate how many should be returns vs purchases
    target_purchases = customer.total_purchases
    target_returns = customer.total_returns
    total_needed = target_purchases + target_returns

    # Scale to fit our row count
    scale = num_transactions / total_needed
    num_purchase_rows = max(1, int(target_purchases * scale))
    num_return_rows = max(1, int(target_returns * scale))
    num_purchase_rows = num_transactions - num_return_rows

    # Generate purchase amounts that sum close to total_purchase_amount
    purchase_amounts = []
    remaining = customer.total_purchase_amount
    for i in range(num_purchase_rows):
        if i == num_purchase_rows - 1:
            amt = remaining
        else:
            avg = remaining / (num_purchase_rows - i)
            amt = round(random.uniform(avg * 0.3, avg * 1.7), 2)
            amt = min(amt, remaining - (num_purchase_rows - i - 1) * 5)
            amt = max(5.0, amt)
        purchase_amounts.append(round(amt, 2))
        remaining -= amt

    # Generate return amounts that sum close to total_return_amount
    return_amounts = []
    remaining = customer.total_return_amount
    for i in range(num_return_rows):
        if i == num_return_rows - 1:
            amt = remaining
        else:
            avg = remaining / (num_return_rows - i)
            amt = round(random.uniform(avg * 0.3, avg * 1.7), 2)
            amt = min(amt, remaining - (num_return_rows - i - 1) * 5)
            amt = max(5.0, amt)
        return_amounts.append(round(amt, 2))
        remaining -= amt

    # Create transactions
    transactions = []
    base_date = datetime.now() - timedelta(days=365)

    for amt in purchase_amounts:
        date = base_date + timedelta(days=random.randint(0, 360))
        item = random.choice(ITEMS)[0]
        transactions.append((date, item, amt, "Purchase", f"PUR-{random.randint(10000, 99999)}"))

    for amt in return_amounts:
        date = base_date + timedelta(days=random.randint(0, 360))
        item = random.choice(ITEMS)[0]
        transactions.append((date, item, amt, "Return", f"RTN-{random.randint(10000, 99999)}"))

    # Sort by date
    transactions.sort(key=lambda t: t[0])

    # Write rows
    purchase_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
    return_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")

    for i, (date, item, amount, txn_type, ref) in enumerate(transactions):
        row = i + 4
        ws.cell(row=row, column=1, value=date.strftime("%Y-%m-%d")).border = thin_border
        ws.cell(row=row, column=2, value=item).border = thin_border
        cell_amt = ws.cell(row=row, column=3, value=amount)
        cell_amt.number_format = "$#,##0.00"
        cell_amt.border = thin_border
        cell_type = ws.cell(row=row, column=4, value=txn_type)
        cell_type.border = thin_border
        ws.cell(row=row, column=5, value=ref).border = thin_border

        fill = purchase_fill if txn_type == "Purchase" else return_fill
        for col in range(1, 6):
            ws.cell(row=row, column=col).fill = fill

    # Column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 25
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 14

    output_path = output_dir / "transactions.xlsx"
    wb.save(str(output_path))
    return output_path


def generate_fraud_report_pptx(customer: Customer, output_dir: Path) -> Path:
    """Generate fraud analyst report PowerPoint with buried assessment."""
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    num_filler_slides = random.randint(5, 9)
    assessment_position = random.randint(1, num_filler_slides)  # 1-indexed

    filler_titles = random.sample(FILLER_SLIDE_TITLES, min(num_filler_slides, len(FILLER_SLIDE_TITLES)))

    slide_index = 0
    for i in range(num_filler_slides + 1):
        slide_layout = prs.slide_layouts[1]  # Title and Content
        slide = prs.slides.add_slide(slide_layout)

        if i == assessment_position:
            # The real assessment slide
            slide.shapes.title.text = "Individual Customer Assessment"
            body = slide.placeholders[1]
            tf = body.text_frame
            tf.clear()

            # Add some filler text before the key info
            p = tf.paragraphs[0]
            p.text = "Based on behavioral analysis and transaction pattern review:"
            p.font.size = Pt(14)

            p = tf.add_paragraph()
            p.text = ""

            p = tf.add_paragraph()
            p.text = f"Customer: {customer.name} ({customer.id})"
            p.font.size = Pt(14)

            p = tf.add_paragraph()
            p.text = f"Assessment: {customer.fraud_assessment.value.upper().replace('_', ' ')}"
            p.font.size = Pt(16)
            p.font.bold = True

            p = tf.add_paragraph()
            p.text = f"Spending Potential: {customer.spending_potential.value.upper()}"
            p.font.size = Pt(16)
            p.font.bold = True

            p = tf.add_paragraph()
            p.text = ""

            p = tf.add_paragraph()
            p.text = "Note: Assessment generated via automated pipeline v4.2"
            p.font.size = Pt(10)
            p.font.italic = True
        else:
            # Filler slide
            title_text = filler_titles[slide_index % len(filler_titles)]
            slide.shapes.title.text = title_text

            body = slide.placeholders[1]
            tf = body.text_frame
            tf.clear()

            num_bullets = random.randint(3, 6)
            bullets = random.sample(FILLER_BULLETS, min(num_bullets, len(FILLER_BULLETS)))

            for j, bullet in enumerate(bullets):
                if j == 0:
                    tf.paragraphs[0].text = bullet
                    tf.paragraphs[0].font.size = Pt(14)
                else:
                    p = tf.add_paragraph()
                    p.text = bullet
                    p.font.size = Pt(14)

            slide_index += 1

    output_path = output_dir / "fraud_report.pptx"
    prs.save(str(output_path))
    return output_path


def load_generated_customers(count: int | None = None) -> list[Customer]:
    """Load pre-generated customer profiles from disk."""
    if not GENERATED_DIR.exists():
        return []

    customers: list[Customer] = []
    profile_paths = sorted(GENERATED_DIR.glob("CUST-*/profile.json"))
    for profile_path in profile_paths:
        customers.append(Customer.model_validate_json(profile_path.read_text()))
        if count is not None and len(customers) >= count:
            break

    return customers


def generate_all_customers(count: int = 50) -> list[Customer]:
    """Generate all customers and their associated files."""
    GENERATED_DIR.mkdir(exist_ok=True)

    customers = []
    for i in range(count):
        customer = generate_customer(i)
        customers.append(customer)

        # Create customer directory
        customer_dir = GENERATED_DIR / customer.id
        customer_dir.mkdir(exist_ok=True)

        # Save profile JSON
        with open(customer_dir / "profile.json", "w") as f:
            json.dump(customer.model_dump(), f, indent=2)

        # Generate files
        generate_receipt_pdf(customer, customer_dir)
        generate_transactions_xlsx(customer, customer_dir)
        generate_fraud_report_pptx(customer, customer_dir)

    return customers


if __name__ == "__main__":
    print("Generating customers...")
    customers = generate_all_customers(50)
    print(f"Generated {len(customers)} customers in {GENERATED_DIR}")

    # Print decision distribution
    accept = sum(1 for c in customers if c.correct_decision == Decision.ACCEPT)
    deny = sum(1 for c in customers if c.correct_decision == Decision.DENY)
    print(f"Decision distribution: {accept} ACCEPT, {deny} DENY")
