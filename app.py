from models import (
    db,
    User,
    Customer,
    Supplier,
    Product,
    Purchase,
    Sale,
    SaleItem,
    Payment,
    StockMovement,
    AuditLog,
    StoreSettings,
    Expense,
    IMEIRecord,
)

from io import BytesIO
from flask import make_response, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

import os
import math
import threading
import time as time_module
import urllib.request

from datetime import datetime, time, timedelta
from functools import wraps
from xml.sax.saxutils import escape

from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    flash,
    current_app,
    jsonify,
)
from flask_login import (
    LoginManager,
    current_user,
    login_user,
    logout_user,
    login_required,
)
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import or_, func

from config import Config

login_manager = LoginManager()
csrf = CSRFProtect()

ROLES = {"admin", "staff"}


# --------------------------------------------------
# APPLICATION
# --------------------------------------------------


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "login_page"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "warning"

    # --------------------------------------------------
    # ROLE AUTHORIZATION
    # --------------------------------------------------

    def roles_required(*allowed_roles):
        def decorator(view):
            @wraps(view)
            @login_required
            def wrapped(*args, **kwargs):
                if current_user.role not in allowed_roles:
                    flash(
                        "You do not have permission to access this page.",
                        "danger",
                    )
                    return redirect(url_for("dashboard_page"))

                return view(*args, **kwargs)

            return wrapped

        return decorator

    # --------------------------------------------------
    # ADMIN: ANALYTICS
    # --------------------------------------------------

    @app.route("/analytics")
    @login_required
    @roles_required("admin")
    def analytics_page():
        total_sales = Sale.query.count()

        sales_value = db.session.query(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar()

        completed_payments = (
            db.session.query(func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.status == "completed")
            .scalar()
        )

        return render_template(
            "admin/analytics.html",
            total_sales=total_sales,
            sales_value=float(sales_value or 0),
            completed_payments=float(completed_payments or 0),
            total_customers=Customer.query.count(),
            total_products=Product.query.count(),
            total_users=User.query.count(),
        )

    @app.route("/reports/pdf/<report_type>")
    @login_required
    @roles_required("admin")
    def report_pdf(report_type):
        allowed_reports = {
            "sales": "Sales Report",
            "inventory": "Inventory Report",
            "purchases": "Purchase Report",
            "customers": "Customer Report",
            "expenses": "Expense Report",
            "imei": "IMEI Report",
        }

        if report_type not in allowed_reports:
            return "Invalid report type.", 404

        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
            end_dt = (
                datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                if end_date
                else None
            )

            if start_dt and end_dt and start_dt >= end_dt:
                return "Start date must be before or equal to end date.", 400

        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD.", 400

        def apply_dates(query, column):
            if start_dt:
                query = query.filter(column >= start_dt)
            if end_dt:
                query = query.filter(column < end_dt)
            return query

        def money(value):
            return f"Rs. {float(value or 0):,.2f}"

        def display_date(value):
            return value.strftime("%d-%m-%Y") if value else "-"

        headers = []
        rows = []

        if report_type == "sales":
            headers = ["Date", "Invoice", "Customer", "Amount", "Status"]
            sales = apply_dates(
                Sale.query.order_by(Sale.sale_date.desc()),
                Sale.sale_date,
            ).all()

            for sale in sales:
                rows.append(
                    [
                        display_date(sale.sale_date),
                        sale.invoice_number or "-",
                        sale.customer.name if sale.customer else "Walk-in",
                        money(sale.total_amount),
                        (sale.status or "Unknown").title(),
                    ]
                )

        elif report_type == "inventory":
            headers = [
                "Code",
                "Product",
                "Brand",
                "Category",
                "Stock",
                "Cost Price",
                "Selling Price",
                "Stock Value",
            ]
            products = Product.query.order_by(Product.name).all()

            for product in products:
                quantity = product.stock_quantity or 0
                cost = product.cost_price or 0

                rows.append(
                    [
                        product.product_code or "-",
                        product.name or "-",
                        product.brand or "-",
                        product.category or "-",
                        str(quantity),
                        money(cost),
                        money(product.selling_price),
                        money(quantity * cost),
                    ]
                )

        elif report_type == "purchases":
            headers = ["Date", "Invoice", "Supplier", "Amount", "Status"]
            purchases = apply_dates(
                Purchase.query.order_by(Purchase.purchase_date.desc()),
                Purchase.purchase_date,
            ).all()

            for purchase in purchases:
                rows.append(
                    [
                        display_date(purchase.purchase_date),
                        purchase.invoice_number or "-",
                        purchase.supplier.name if purchase.supplier else "-",
                        money(purchase.total_amount),
                        (purchase.status or "Unknown").title(),
                    ]
                )

        elif report_type == "customers":
            headers = ["Name", "Phone", "Email", "GST Number", "Added"]
            customers = Customer.query.order_by(Customer.name).all()

            if start_dt:
                customers = [
                    c
                    for c in customers
                    if c.created_at and c.created_at.replace(tzinfo=None) >= start_dt
                ]

            if end_dt:
                customers = [
                    c
                    for c in customers
                    if c.created_at and c.created_at.replace(tzinfo=None) < end_dt
                ]

            for customer in customers:
                rows.append(
                    [
                        customer.name or "-",
                        customer.phone or "-",
                        customer.email or "-",
                        customer.gst_number or "-",
                        display_date(customer.created_at),
                    ]
                )

        elif report_type == "expenses":
            headers = ["Date", "Title", "Category", "Amount", "Notes"]
            expenses = apply_dates(
                Expense.query.order_by(Expense.expense_date.desc()),
                Expense.expense_date,
            ).all()

            for expense in expenses:
                rows.append(
                    [
                        display_date(expense.expense_date),
                        expense.title or "-",
                        expense.category or "-",
                        money(expense.amount),
                        expense.notes or "-",
                    ]
                )

        elif report_type == "imei":
            headers = ["IMEI", "Product Code", "Product", "Brand", "Status"]
            records = IMEIRecord.query.order_by(IMEIRecord.id.desc()).all()

            for record in records:
                product = record.product
                rows.append(
                    [
                        record.imei or "-",
                        product.product_code if product else "-",
                        product.name if product else "-",
                        product.brand if product and product.brand else "-",
                        (record.status or "Unknown").replace("_", " ").title(),
                    ]
                )

        if not rows:
            rows = [["No records found for this report."] + [""] * (len(headers) - 1)]

        # Build a PDF in memory.
        buffer = BytesIO()
        page_size = (
            landscape(A4) if report_type in ("inventory", "customers", "imei") else A4
        )

        document = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            rightMargin=30,
            leftMargin=30,
            topMargin=35,
            bottomMargin=35,
            title=allowed_reports[report_type],
        )

        styles = getSampleStyleSheet()
        story = [
            Paragraph(escape(allowed_reports[report_type]), styles["Title"]),
            Spacer(1, 12),
        ]

        if start_date or end_date:
            period = (
                f"Period: {start_date or 'Beginning'} to " f"{end_date or 'Present'}"
            )
            story.extend(
                [
                    Paragraph(escape(period), styles["Normal"]),
                    Spacer(1, 10),
                ]
            )

        # Paragraph cells allow long text to wrap inside the PDF.
        table_data = [
            [Paragraph(escape(str(header)), styles["Normal"]) for header in headers]
        ]

        for row in rows:
            table_data.append(
                [Paragraph(escape(str(value)), styles["BodyText"]) for value in row]
            )

        available_width = page_size[0] - 60
        column_width = available_width / len(headers)

        table = Table(
            table_data,
            colWidths=[column_width] * len(headers),
            repeatRows=1,
            hAlign="LEFT",
        )

        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2457A7")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [
                            colors.white,
                            colors.HexColor("#EEF4FC"),
                        ],
                    ),
                ]
            )
        )

        story.append(table)
        document.build(story)

        response = make_response(buffer.getvalue())
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{report_type}_report.pdf"'
        )

        return response

    @app.route("/about")
    @login_required
    def about_page():
        return render_template("about.html")

        # --------------------------------------------------
        # ADMIN: REPORTS
        # --------------------------------------------------

    @app.route("/reports")
    @login_required
    @roles_required("admin")
    def reports_page():
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        query = Sale.query

        try:
            query = apply_date_filters(query, Sale, date_from, date_to)
        except ValueError:
            flash("Please enter valid report dates.", "warning")
            return redirect(url_for("reports_page"))

        sales = query.order_by(Sale.id.desc()).all()

        payments = Payment.query.filter_by(status="completed").all()

        paid_by_sale = {}
        for payment in payments:
            paid_by_sale[payment.sale_id] = paid_by_sale.get(
                payment.sale_id, 0.0
            ) + float(payment.amount or 0)

        report_total = sum(float(sale.total_amount or 0) for sale in sales)

        report_paid = sum(paid_by_sale.get(sale.id, 0.0) for sale in sales)

        return render_template(
            "admin/reports.html",
            sales=sales,
            date_from=date_from,
            date_to=date_to,
            report_count=len(sales),
            report_total=report_total,
            report_paid=report_paid,
            report_balance=max(report_total - report_paid, 0.0),
            paid_by_sale=paid_by_sale,
        )

    # --------------------------------------------------
    # ADMIN: USER ACCOUNTS
    # --------------------------------------------------

    @app.route("/users")
    @login_required
    @roles_required("admin")
    def users_accounts_page():
        users = User.query.order_by(User.id.asc()).all()

        return render_template(
            "admin/users.html",
            users=users,
        )

    @app.route("/users/<int:user_id>/delete", methods=["POST"])
    @login_required
    @roles_required("admin")
    def delete_user(user_id):
        user = db.session.get(User, user_id)

        if user is None:
            flash("User not found.", "warning")
            return redirect(url_for("users_accounts_page"))

        # Protect every admin account.
        if (user.role or "").lower() == "admin":
            flash("Admin accounts cannot be deleted.", "danger")
            return redirect(url_for("users_accounts_page"))

        # Prevent deleting the currently logged-in user.
        if user.id == current_user.id:
            flash("You cannot delete your own account.", "danger")
            return redirect(url_for("users_accounts_page"))

        db.session.delete(user)
        db.session.commit()

        flash("Staff account deleted successfully.", "success")
        return redirect(url_for("users_accounts_page"))

    # --------------------------------------------------
    # LOGIN MANAGER
    # --------------------------------------------------

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    # --------------------------------------------------
    # SHARED HELPERS
    # --------------------------------------------------

    def apply_date_filters(query, model, date_from, date_to):
        """Apply date filters when the model has created_at."""
        if not hasattr(model, "created_at"):
            if date_from or date_to:
                flash(
                    "Date filtering is unavailable for this record type.",
                    "warning",
                )
            return query

        try:
            if date_from:
                start = datetime.strptime(date_from, "%Y-%m-%d").date()
                query = query.filter(
                    model.created_at >= datetime.combine(start, time.min)
                )

            if date_to:
                end = datetime.strptime(date_to, "%Y-%m-%d").date()
                query = query.filter(
                    model.created_at <= datetime.combine(end, time.max)
                )

        except ValueError:
            raise ValueError("Please enter valid filter dates.")

        return query

    def parse_number(field_name, default, converter):
        """Read and validate a numeric form field."""
        raw = request.form.get(field_name, "").strip()

        if raw == "":
            return default

        try:
            value = converter(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Enter a valid value for {field_name.replace('_', ' ')}.")

        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Enter a valid value for {field_name.replace('_', ' ')}.")

        return value

    # --------------------------------------------------
    # PRODUCT FORM VALIDATION HELPER
    # --------------------------------------------------

    def product_form_values():
        """Validate and return values matching the Product model."""

        product_code = request.form.get("product_code", "").strip()
        name = request.form.get("name", "").strip()
        condition = request.form.get("condition", "Sealed").strip().title()

        if condition not in {"Sealed", "Used"}:
            raise ValueError("Choose Sealed or Used.")

        if not product_code:
            while True:
                product_code = (
                    f"PRD-{datetime.now().strftime('%y%m%d%H%M%S')}-"
                    f"{os.urandom(2).hex().upper()}"
                )
                if not Product.query.filter_by(product_code=product_code).first():
                    break

        if len(product_code) > 50:
            raise ValueError("Product code must be 50 characters or fewer.")

        if not name:
            raise ValueError("Product name is required.")

        if len(name) > 150:
            raise ValueError("Product name must be 150 characters or fewer.")

        def number(field, default=0.0):
            raw = request.form.get(field, "").strip()
            if not raw:
                return float(default)

            try:
                value = float(raw)
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Enter a valid number for {field.replace('_', ' ')}.")

            if not math.isfinite(value):
                raise ValueError(f"Enter a valid number for {field.replace('_', ' ')}.")

            return value

        def integer(field, default=0):
            value = number(field, default)
            if not value.is_integer():
                raise ValueError(
                    f"{field.replace('_', ' ').title()} must be a whole number."
                )
            return int(value)

        cost_price = number("cost_price")
        selling_price = number("selling_price")
        gst_percent = number("gst_percent")
        stock_quantity = integer("stock_quantity")
        reorder_level = integer("reorder_level", 5)
        warranty_months = integer("warranty_months", 0)

        if cost_price < 0 or selling_price < 0:
            raise ValueError("Prices cannot be negative.")

        if not 0 <= gst_percent <= 100:
            raise ValueError("GST must be between 0 and 100.")

        if stock_quantity < 0 or reorder_level < 0:
            raise ValueError("Stock and reorder level cannot be negative.")

        if not 0 <= warranty_months <= 120:
            raise ValueError("Warranty must be between 0 and 120 months.")

        used_grade = request.form.get("used_grade", "").strip() or None

        if condition == "Used":
            allowed_grades = {"Excellent", "Good", "Fair", "For Parts"}
            if used_grade not in allowed_grades:
                raise ValueError("Choose a valid grade for the used phone.")
        else:
            used_grade = None

        barcode = request.form.get("barcode", "").strip() or None
        if barcode and len(barcode) > 100:
            raise ValueError("Barcode must be 100 characters or fewer.")

        return {
            "product_code": product_code,
            "barcode": barcode,
            "name": name,
            "brand": request.form.get("brand", "").strip() or None,
            "model": request.form.get("model", "").strip() or None,
            "category": request.form.get("category", "Mobile").strip() or "Mobile",
            "variant": request.form.get("variant", "").strip() or None,
            "color": request.form.get("color", "").strip() or None,
            "condition": condition,
            "used_grade": used_grade,
            "warranty_months": warranty_months,
            "hsn_code": request.form.get("hsn_code", "").strip() or None,
            "gst_percent": gst_percent,
            "cost_price": cost_price,
            "selling_price": selling_price,
            "stock_quantity": stock_quantity,
            "reorder_level": reorder_level,
            "track_imei": "track_imei" in request.form,
            "is_active": "is_active" in request.form,
        }

    # --------------------------------------------------
    # PRODUCTS: DEACTIVATE
    # --------------------------------------------------

    @app.route("/products/<int:product_id>/deactivate", methods=["POST"])
    @login_required
    def product_deactivate(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        product.is_active = False

        try:
            db.session.commit()
            flash(f"{product.name} has been deactivated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Product deactivation failed")
            flash("Could not deactivate the product.", "danger")

        return redirect(url_for("products_list"))

    # --------------------------------------------------
    # PRODUCTS: CREATE
    # --------------------------------------------------

    @app.route("/products/create", methods=["GET", "POST"])
    @login_required
    def product_create():
        selected_type = (
            (request.args.get("type") or request.form.get("condition") or "Sealed")
            .strip()
            .title()
        )

        if selected_type not in {"Sealed", "Used"}:
            selected_type = "Sealed"

        if request.method == "POST":
            try:
                values = product_form_values()

                # Read IMEI numbers from the product form.
                imei1 = request.form.get("imei1", "").strip()
                imei2 = request.form.get("imei2", "").strip()

                # IMEI 1 is required when IMEI tracking is enabled.
                if values.get("track_imei", True) and not imei1:
                    raise ValueError(
                        "IMEI Number 1 is required when IMEI tracking is enabled."
                    )

                # Validate both IMEI numbers.
                for label, imei in (
                    ("IMEI Number 1", imei1),
                    ("IMEI Number 2", imei2),
                ):
                    if imei and (len(imei) != 15 or not imei.isdigit()):
                        raise ValueError(f"{label} must contain exactly 15 digits.")

                if imei1 and imei2 and imei1 == imei2:
                    raise ValueError(
                        "IMEI Number 1 and IMEI Number 2 cannot be the same."
                    )

                # Check both IMEIs against both database columns.
                for imei in (imei1, imei2):
                    if not imei:
                        continue

                    duplicate = IMEIRecord.query.filter(
                        db.or_(
                            IMEIRecord.imei1 == imei,
                            IMEIRecord.imei2 == imei,
                        )
                    ).first()

                    if duplicate:
                        raise ValueError(f"IMEI {imei} is already registered.")

                # Keep your existing product-code and barcode checks here.

                if imei1:
                    values["track_imei"] = True

                # Create product.
                product = Product(**values)
                db.session.add(product)
                db.session.flush()

                # One record per physical phone, storing both IMEIs.
                if imei1:
                    db.session.add(
                        IMEIRecord(
                            product_id=product.id,
                            imei1=imei1,
                            imei2=imei2 or None,
                            status="in_stock",
                        )
                    )

                db.session.commit()

                flash(
                    "Product created successfully.",
                    "success",
                )
                return redirect(url_for("products_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Product creation failed")
                flash(
                    "Could not save the product. "
                    "Check the application log for details.",
                    "danger",
                )

        return render_template(
            "products/form.html",
            product=None,
            selected_type=selected_type,
        )

    @app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        selected_type = product.condition or "Sealed"

        if request.method == "POST":
            try:
                values = product_form_values()

                # Prevent duplicate product codes on other products.
                existing_code = Product.query.filter(
                    Product.product_code == values["product_code"],
                    Product.id != product.id,
                ).first()

                if existing_code:
                    raise ValueError("This product code already exists.")

                # Prevent duplicate barcodes on other products.
                if values.get("barcode"):
                    existing_barcode = Product.query.filter(
                        Product.barcode == values["barcode"],
                        Product.id != product.id,
                    ).first()

                    if existing_barcode:
                        raise ValueError("This barcode already exists.")

                # Update the existing product.
                for field, value in values.items():
                    setattr(product, field, value)

                db.session.commit()

                flash("Product updated successfully.", "success")
                return redirect(url_for("products_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Product update failed")

                flash(
                    "Could not update the product. "
                    "Check the application log for details.",
                    "danger",
                )

        return render_template(
            "products/form.html",
            product=product,
            selected_type=selected_type,
        )

    # --------------------------------------------------
    # PRODUCTS: BARCODE LOOKUP
    # --------------------------------------------------

    @app.route("/products/barcode-lookup")
    @login_required
    def product_barcode_lookup():
        barcode = request.args.get("barcode", "").strip()

        if not barcode:
            return (
                jsonify(
                    {
                        "found": False,
                        "message": "Enter or scan a barcode.",
                    }
                ),
                400,
            )

        product = Product.query.filter_by(barcode=barcode).first()

        if not product:
            return jsonify(
                {
                    "found": False,
                    "message": "Barcode not found. You can enter the details manually.",
                }
            )

        return jsonify(
            {
                "found": True,
                "product": {
                    "product_code": product.product_code,
                    "barcode": product.barcode or "",
                    "name": product.name or "",
                    "brand": product.brand or "",
                    "model": product.model or "",
                    "category": product.category or "Mobile",
                    "color": product.color or "",
                    "ram": product.ram or "",
                    "storage": product.storage or "",
                    "hsn_code": product.hsn_code or "",
                    "gst_percent": product.gst_percent or 0,
                    "cost_price": product.cost_price or 0,
                    "selling_price": product.selling_price or 0,
                    "product_type": product.product_type or "Sealed",
                    "condition": product.condition or "",
                    "warranty_months": product.warranty_months or "",
                },
            }
        )

    # --------------------------------------------------
    # IMEI RECORDS: LIST AND TRACKING
    # --------------------------------------------------

    @app.route("/imei-records")
    @login_required
    def imei_records_list():
        search = request.args.get("q", "").strip()
        selected_status = request.args.get("status", "").strip()

        allowed_statuses = {
            "in_stock",
            "sold",
            "returned",
            "damaged",
        }

        query = IMEIRecord.query.join(
            Product,
            IMEIRecord.product_id == Product.id,
        )

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                db.or_(
                    IMEIRecord.imei1.ilike(pattern),
                    IMEIRecord.imei2.ilike(pattern),
                    Product.name.ilike(pattern),
                    Product.product_code.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                )
            )

        if selected_status in allowed_statuses:
            query = query.filter(IMEIRecord.status == selected_status)
        else:
            selected_status = ""

        records = query.order_by(IMEIRecord.id.desc()).all()

        counts = {
            "total": IMEIRecord.query.count(),
            "in_stock": IMEIRecord.query.filter_by(status="in_stock").count(),
            "sold": IMEIRecord.query.filter_by(status="sold").count(),
            "returned": IMEIRecord.query.filter_by(status="returned").count(),
            "damaged": IMEIRecord.query.filter_by(status="damaged").count(),
        }

        return render_template(
            "imei/list.html",
            records=records,
            search=search,
            selected_status=selected_status,
            counts=counts,
        )

    # --------------------------------------------------
    # BILLING: LIST AND SEARCH
    # --------------------------------------------------

    @app.route("/billing")
    @login_required
    def billing_list():
        search = request.args.get("search", "").strip()
        query = Sale.query

        if search:
            pattern = f"%{search}%"
            query = query.outerjoin(Customer).filter(
                or_(
                    Sale.invoice_number.ilike(pattern),
                    Customer.name.ilike(pattern),
                    Customer.phone.ilike(pattern),
                )
            )

        sales = query.order_by(Sale.id.desc()).all()

        payment_totals = {}
        for sale in sales:
            payment_totals[sale.id] = sum(
                float(payment.amount or 0)
                for payment in Payment.query.filter_by(sale_id=sale.id).all()
            )

        return render_template(
            "billing/list.html",
            sales=sales,
            search=search,
            payment_totals=payment_totals,
        )

    # --------------------------------------------------
    # BILLING: CREATE INVOICE
    # --------------------------------------------------

    @app.route("/billing/create", methods=["GET", "POST"])
    @login_required
    def billing_create():
        customers = Customer.query.order_by(Customer.name.asc()).all()
        products = (
            Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
        )

        if request.method == "POST":
            customer_value = request.form.get("customer_id", "").strip()
            discount_text = request.form.get("discount_amount", "0").strip()
            payment_method = request.form.get("payment_method", "cash").strip().lower()
            reference = request.form.get("payment_reference", "").strip()

            try:
                customer_id = int(customer_value) if customer_value else None
                discount = float(discount_text or 0)
                paid_amount = float(request.form.get("paid_amount", "0") or 0)
            except (ValueError, TypeError):
                flash(
                    "Enter valid customer, discount, and payment values.",
                    "danger",
                )
                return redirect(url_for("billing_create"))

            if (
                customer_id is not None
                and db.session.get(Customer, customer_id) is None
            ):
                flash("Selected customer was not found.", "danger")
                return redirect(url_for("billing_create"))

            if discount < 0 or paid_amount < 0:
                flash(
                    "Discount and payment cannot be negative.",
                    "danger",
                )
                return redirect(url_for("billing_create"))

            allowed_methods = {"cash", "upi", "card", "bank", "credit"}
            if payment_method not in allowed_methods:
                flash("Select a valid payment method.", "danger")
                return redirect(url_for("billing_create"))

            product_ids = request.form.getlist("product_id[]")
            quantities = request.form.getlist("quantity[]")

            if not product_ids or len(product_ids) != len(quantities):
                flash("Add at least one product to the bill.", "danger")
                return redirect(url_for("billing_create"))

            try:
                requested_items = {}

                for product_id_text, quantity_text in zip(product_ids, quantities):
                    if not product_id_text:
                        continue

                    product_id = int(product_id_text)
                    quantity = int(quantity_text)

                    if quantity <= 0:
                        raise ValueError("Product quantity must be at least 1.")

                    requested_items[product_id] = (
                        requested_items.get(product_id, 0) + quantity
                    )

                if not requested_items:
                    raise ValueError("Add at least one valid product.")

                selected_products = (
                    Product.query.filter(Product.id.in_(requested_items.keys()))
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )
                product_map = {product.id: product for product in selected_products}

                subtotal = 0.0
                tax_total = 0.0
                item_rows = []

                for product_id, quantity in requested_items.items():
                    product = product_map.get(product_id)

                    if product is None or not product.is_active:
                        raise ValueError("A selected product is unavailable.")

                    available_stock = int(product.stock_quantity or 0)
                    if available_stock < quantity:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available: {available_stock}."
                        )

                    unit_price = float(product.selling_price or 0)
                    gst_percent = float(product.gst_percent or 0)

                    if unit_price < 0 or gst_percent < 0:
                        raise ValueError(
                            f"Invalid price or GST rate for {product.name}."
                        )

                    line_subtotal = round(unit_price * quantity, 2)
                    line_tax = round(line_subtotal * gst_percent / 100, 2)

                    subtotal += line_subtotal
                    tax_total += line_tax

                    item_rows.append(
                        {
                            "product": product,
                            "quantity": quantity,
                            "unit_price": unit_price,
                            "subtotal": line_subtotal,
                            "tax_amount": line_tax,
                        }
                    )

                subtotal = round(subtotal, 2)
                tax_total = round(tax_total, 2)

                if discount > subtotal + tax_total:
                    raise ValueError("Discount cannot exceed the bill total.")

                grand_total = round(subtotal + tax_total - discount, 2)

                if paid_amount > grand_total:
                    raise ValueError("Paid amount cannot exceed the bill total.")

                invoice_number = (
                    f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}-"
                    f"{current_user.id}-{os.urandom(2).hex().upper()}"
                )

                sale = Sale(
                    invoice_number=invoice_number,
                    customer_id=customer_id,
                    user_id=current_user.id,
                    subtotal=subtotal,
                    tax_amount=tax_total,
                    discount_amount=round(discount, 2),
                    total_amount=grand_total,
                    status="completed",
                )
                db.session.add(sale)
                db.session.flush()

                for row in item_rows:
                    product = row["product"]
                    quantity = row["quantity"]

                    db.session.add(
                        SaleItem(
                            sale_id=sale.id,
                            product_id=product.id,
                            quantity=quantity,
                            unit_price=row["unit_price"],
                            tax_amount=row["tax_amount"],
                            subtotal=row["subtotal"],
                        )
                    )

                    product.stock_quantity = int(product.stock_quantity or 0) - quantity

                    db.session.add(
                        StockMovement(
                            product_id=product.id,
                            user_id=current_user.id,
                            movement_type="sale",
                            quantity_change=-quantity,
                            reference=invoice_number,
                            notes=(f"Stock deducted for invoice {invoice_number}"),
                        )
                    )

                if paid_amount > 0:
                    db.session.add(
                        Payment(
                            sale_id=sale.id,
                            amount=round(paid_amount, 2),
                            method=payment_method,
                            reference=reference or None,
                            status="completed",
                        )
                    )

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="Sale",
                        entity_id=sale.id,
                        details=(
                            f"Created invoice {invoice_number}; "
                            f"total {grand_total:.2f}"
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash(
                    f"Invoice {invoice_number} created successfully.",
                    "success",
                )
                return redirect(url_for("billing_detail", sale_id=sale.id))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("billing_create"))

            except Exception:
                db.session.rollback()
                app.logger.exception("Billing transaction failed")
                flash(
                    "Could not save the bill. No changes were committed.",
                    "danger",
                )
                return redirect(url_for("billing_create"))

        return render_template(
            "billing/form.html",
            customers=customers,
            products=products,
        )

    # --------------------------------------------------
    # BILLING: EDIT EXISTING INVOICE
    # --------------------------------------------------

    @app.route(
        "/billing/<int:sale_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_edit(sale_id):
        sale = db.session.get(Sale, sale_id)

        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        customers = Customer.query.order_by(Customer.name.asc()).all()
        products = (
            Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
        )

        if request.method == "POST":
            try:
                customer_value = request.form.get("customer_id", "").strip()
                discount = float(request.form.get("discount_amount", "0") or 0)
                customer_id = int(customer_value) if customer_value else None

                if (
                    customer_id is not None
                    and db.session.get(Customer, customer_id) is None
                ):
                    raise ValueError("Selected customer was not found.")

                if discount < 0:
                    raise ValueError("Discount cannot be negative.")

                product_ids = request.form.getlist("product_id[]")
                quantities = request.form.getlist("quantity[]")

                if not product_ids or len(product_ids) != len(quantities):
                    raise ValueError("Add at least one product to the invoice.")

                requested_items = {}
                for product_id_text, quantity_text in zip(product_ids, quantities):
                    if not product_id_text:
                        continue

                    product_id = int(product_id_text)
                    quantity = int(quantity_text)

                    if quantity <= 0:
                        raise ValueError("Quantity must be at least 1.")

                    requested_items[product_id] = (
                        requested_items.get(product_id, 0) + quantity
                    )

                if not requested_items:
                    raise ValueError("Add at least one valid product.")

                # Lock selected product rows.
                selected_products = (
                    Product.query.filter(Product.id.in_(requested_items.keys()))
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )
                product_map = {product.id: product for product in selected_products}

                if len(product_map) != len(requested_items):
                    raise ValueError("One or more selected products were not found.")

                for product in selected_products:
                    if not product.is_active:
                        raise ValueError(
                            f"{product.name} is inactive and cannot be added."
                        )

                old_quantities = {}
                for item in sale.items:
                    old_quantities[item.product_id] = old_quantities.get(
                        item.product_id, 0
                    ) + int(item.quantity or 0)

                subtotal = 0.0
                tax_total = 0.0
                item_rows = []

                for product_id, quantity in requested_items.items():
                    product = product_map[product_id]

                    available_stock = int(
                        product.stock_quantity or 0
                    ) + old_quantities.get(product_id, 0)
                    if available_stock < quantity:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available for this edit: {available_stock}."
                        )

                    unit_price = float(product.selling_price or 0)
                    gst_percent = float(product.gst_percent or 0)

                    if unit_price < 0 or gst_percent < 0:
                        raise ValueError(
                            f"Invalid price or GST rate for {product.name}."
                        )

                    line_subtotal = round(unit_price * quantity, 2)
                    line_tax = round(line_subtotal * gst_percent / 100, 2)

                    subtotal += line_subtotal
                    tax_total += line_tax

                    item_rows.append(
                        {
                            "product": product,
                            "quantity": quantity,
                            "unit_price": unit_price,
                            "subtotal": line_subtotal,
                            "tax_amount": line_tax,
                        }
                    )

                subtotal = round(subtotal, 2)
                tax_total = round(tax_total, 2)

                if discount > subtotal + tax_total:
                    raise ValueError("Discount cannot exceed the bill total.")

                grand_total = round(subtotal + tax_total - discount, 2)

                paid_amount = round(
                    sum(
                        float(payment.amount or 0)
                        for payment in Payment.query.filter_by(
                            sale_id=sale.id,
                            status="completed",
                        ).all()
                    ),
                    2,
                )

                if grand_total < paid_amount:
                    raise ValueError(
                        f"The revised total (₹{grand_total:.2f}) is below "
                        f"completed payments (₹{paid_amount:.2f}). "
                        "A refund or customer-credit entry is required first."
                    )

                # Lock all products involved, including removed old items.
                all_product_ids = set(old_quantities) | set(requested_items)
                stock_products = (
                    Product.query.filter(Product.id.in_(all_product_ids))
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )
                stock_map = {product.id: product for product in stock_products}

                for product_id in all_product_ids:
                    product = stock_map.get(product_id)
                    if product is None:
                        raise ValueError(
                            "A product on this invoice could not be found."
                        )

                    old_qty = old_quantities.get(product_id, 0)
                    new_qty = requested_items.get(product_id, 0)
                    stock_change = old_qty - new_qty

                    if stock_change:
                        product.stock_quantity = (
                            int(product.stock_quantity or 0) + stock_change
                        )

                        db.session.add(
                            StockMovement(
                                product_id=product_id,
                                user_id=current_user.id,
                                movement_type="sale_adjustment",
                                quantity_change=stock_change,
                                reference=sale.invoice_number,
                                notes=(
                                    f"Invoice {sale.invoice_number} edited. "
                                    f"Previous quantity: {old_qty}; "
                                    f"revised quantity: {new_qty}."
                                ),
                            )
                        )

                # Replace invoice lines; preserve all payment rows.
                for old_item in list(sale.items):
                    db.session.delete(old_item)

                db.session.flush()

                sale.customer_id = customer_id
                sale.subtotal = subtotal
                sale.tax_amount = tax_total
                sale.discount_amount = round(discount, 2)
                sale.total_amount = grand_total

                for row in item_rows:
                    db.session.add(
                        SaleItem(
                            sale_id=sale.id,
                            product_id=row["product"].id,
                            quantity=row["quantity"],
                            unit_price=row["unit_price"],
                            tax_amount=row["tax_amount"],
                            subtotal=row["subtotal"],
                        )
                    )

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="update",
                        entity="Sale",
                        entity_id=sale.id,
                        details=(
                            f"Updated invoice {sale.invoice_number}; "
                            f"revised total {grand_total:.2f}"
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash(
                    f"Invoice {sale.invoice_number} updated successfully.",
                    "success",
                )
                return redirect(url_for("billing_detail", sale_id=sale.id))

            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Invoice edit failed")
                flash(
                    "Could not update the invoice. " "No changes were committed.",
                    "danger",
                )

            return redirect(url_for("billing_edit", sale_id=sale_id))

        return render_template(
            "billing/edit.html",
            sale=sale,
            customers=customers,
            products=products,
        )

    # --------------------------------------------------
    # BILLING: INVOICE DETAILS
    # --------------------------------------------------

    @app.route("/billing/<int:sale_id>")
    @login_required
    def billing_detail(sale_id):
        sale = db.session.get(Sale, sale_id)

        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        payments = (
            Payment.query.filter_by(sale_id=sale.id).order_by(Payment.id.asc()).all()
        )

        # Only completed payments count toward the amount received.
        paid_amount = round(
            sum(
                float(payment.amount or 0)
                for payment in payments
                if (payment.status or "").lower() == "completed"
            ),
            2,
        )
        balance = round(
            max(float(sale.total_amount or 0) - paid_amount, 0.0),
            2,
        )

        return render_template(
            "billing/detail.html",
            sale=sale,
            payments=payments,
            paid_amount=paid_amount,
            balance=balance,
        )

    # --------------------------------------------------
    # BILLING: RECEIVE DUE PAYMENT
    # --------------------------------------------------

    @app.route(
        "/billing/<int:sale_id>/receive-payment",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_receive_payment(sale_id):
        sale = db.session.get(Sale, sale_id)
        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        def get_paid_amount():
            completed_payments = Payment.query.filter_by(
                sale_id=sale_id,
                status="completed",
            ).all()
            return round(
                sum(float(payment.amount or 0) for payment in completed_payments),
                2,
            )

        paid_amount = get_paid_amount()
        total_amount = round(float(sale.total_amount or 0), 2)
        balance = round(max(total_amount - paid_amount, 0.0), 2)

        if balance <= 0.009:
            flash("This invoice has already been fully paid.", "info")
            return redirect(url_for("billing_detail", sale_id=sale_id))

        if request.method == "POST":
            try:
                amount_text = request.form.get("amount", "").strip()
                method = request.form.get("method", "cash").strip().lower()
                reference = request.form.get("reference", "").strip()

                if not amount_text:
                    raise ValueError("Please enter the payment amount.")

                try:
                    amount = float(amount_text)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Enter a valid payment amount.")

                if not math.isfinite(amount) or amount <= 0:
                    raise ValueError("Payment amount must be greater than zero.")

                amount = round(amount, 2)
                if amount <= 0:
                    raise ValueError("Payment amount must be at least ₹0.01.")

                # Keep these options compatible with billing_create().
                allowed_methods = {"cash", "upi", "card", "bank", "credit"}
                if method not in allowed_methods:
                    raise ValueError("Please select a valid payment method.")

                if len(reference) > 100:
                    raise ValueError("Payment reference cannot exceed 100 characters.")

                # Recalculate immediately before recording the payment.
                paid_amount = get_paid_amount()
                balance = round(max(total_amount - paid_amount, 0.0), 2)
                if balance <= 0.009:
                    raise ValueError("This invoice has already been fully paid.")
                if amount > balance + 0.001:
                    raise ValueError(
                        f"Payment cannot exceed the outstanding balance of ₹{balance:.2f}."
                    )

                payment = Payment(
                    sale_id=sale_id,
                    amount=amount,
                    method=method,
                    reference=reference or None,
                    status="completed",
                )
                db.session.add(payment)
                db.session.flush()

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="Payment",
                        entity_id=payment.id,
                        details=(
                            f"Received ₹{amount:.2f} for invoice "
                            f"{sale.invoice_number} using {method}."
                        ),
                        ip_address=request.remote_addr,
                    )
                )
                db.session.commit()

                flash(
                    f"Payment of ₹{amount:.2f} received successfully.",
                    "success",
                )
                return redirect(url_for("billing_detail", sale_id=sale_id))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            except Exception:
                db.session.rollback()
                app.logger.exception("Failed to record invoice payment.")
                flash("Could not record the payment. Please try again.", "danger")

            # Refresh values after validation or database errors.
            sale = db.session.get(Sale, sale_id)
            if sale is None:
                flash("Invoice not found.", "danger")
                return redirect(url_for("billing_list"))
            total_amount = round(float(sale.total_amount or 0), 2)
            paid_amount = get_paid_amount()
            balance = round(max(total_amount - paid_amount, 0.0), 2)
            if balance <= 0.009:
                return redirect(url_for("billing_detail", sale_id=sale_id))

        return render_template(
            "billing/receive_payment.html",
            sale=sale,
            paid_amount=paid_amount,
            balance=balance,
        )

    # --------------------------------------------------
    # STORE SETTINGS
    # --------------------------------------------------

    # --------------------------------------------------
    # STORE SETTINGS
    # --------------------------------------------------

    @app.route("/store-settings", methods=["GET", "POST"])
    @login_required
    def store_settings_page():
        settings = db.session.get(StoreSettings, 1)

        if settings is None:
            settings = StoreSettings(id=1)
            db.session.add(settings)
            db.session.commit()

        if request.method == "POST":
            try:
                store_name = request.form.get("store_name", "").strip()
                address = request.form.get("address", "").strip()
                phone = request.form.get("phone", "").strip()
                gstin = request.form.get("gstin", "").strip()
                paper_size = request.form.get("paper_size", "A4").strip()
                orientation = request.form.get("orientation", "portrait").strip()
                footer_text = request.form.get("footer_text", "").strip()

                if not store_name:
                    raise ValueError("Store name is required.")

                if paper_size not in {"A4", "A5", "thermal", "custom"}:
                    raise ValueError("Invalid paper size.")

                if orientation not in {"portrait", "landscape"}:
                    raise ValueError("Invalid paper orientation.")

                settings.store_name = store_name[:150]
                settings.address = address or None
                settings.phone = phone[:30] or None
                settings.gstin = gstin[:30] or None
                settings.paper_size = paper_size
                settings.orientation = orientation
                settings.footer_text = footer_text[:500] or None

                if paper_size == "custom":
                    try:
                        width = float(request.form.get("custom_width_mm", "100"))
                        height = float(request.form.get("custom_height_mm", "150"))
                    except (TypeError, ValueError):
                        raise ValueError("Enter valid custom paper dimensions.")

                    if (
                        not math.isfinite(width)
                        or not math.isfinite(height)
                        or not 0 < width <= 1000
                        or not 0 < height <= 1000
                    ):
                        raise ValueError(
                            "Custom dimensions must be greater than 0 "
                            "and no greater than 1000 mm."
                        )

                    settings.custom_width_mm = width
                    settings.custom_height_mm = height

                db.session.commit()
                flash("Store settings saved successfully.", "success")

                return redirect(url_for("store_settings_page"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                current_app.logger.exception("Failed to save store settings.")
                flash(
                    "Could not save store settings. Please try again.",
                    "danger",
                )

            settings = db.session.get(StoreSettings, 1)

        return render_template(
            "store_settings.html",
            settings=settings,
        )

    # --------------------------------------------------
    # HOME
    # --------------------------------------------------

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))
        return redirect(url_for("login_page"))

    # --------------------------------------------------
    # LOGIN
    # --------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            user = User.query.filter_by(username=username).first()

            if user and user.is_active and user.check_password(password):
                login_user(user)
                flash("Welcome back!", "success")
                return redirect(url_for("dashboard_page"))

            flash("Invalid username or password.", "danger")

        return render_template("login.html")

    # --------------------------------------------------
    # PROFILE SETTINGS
    # --------------------------------------------------

    @app.route("/profile-settings", methods=["GET", "POST"])
    @login_required
    def profile_settings():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or len(username) > 80:
                flash(
                    "Enter a valid username (up to 80 characters).",
                    "danger",
                )
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif User.query.filter(
                User.username == username,
                User.id != current_user.id,
            ).first():
                flash("That username is already in use.", "danger")
            elif password and len(password) < 2:
                flash(
                    "New password must contain at least 2 characters.",
                    "danger",
                )
            elif password and password != confirm_password:
                flash("Passwords do not match.", "danger")
            else:
                current_user.username = username
                current_user.full_name = full_name

                if password:
                    current_user.set_password(password)

                try:
                    db.session.commit()
                    flash("Profile updated successfully.", "success")
                    return redirect(url_for("profile_settings"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Profile update failed")
                    flash("Could not update your profile.", "danger")

        return render_template(
            "profile_settings.html",
            user=current_user,
        )

    # --------------------------------------------------
    # REGISTER: NEW USERS ARE STAFF
    # --------------------------------------------------

    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif len(password) < 2:
                flash(
                    "Password must contain at least 2 characters.",
                    "danger",
                )
            elif password != confirm_password:
                flash("Passwords do not match.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                user = User(
                    username=username,
                    full_name=full_name,
                    role="staff",
                    is_active=True,
                )
                user.set_password(password)

                try:
                    db.session.add(user)
                    db.session.commit()
                    flash(
                        "Registration successful. Please log in.",
                        "success",
                    )
                    return redirect(url_for("login_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Registration failed")
                    flash("Could not create your account.", "danger")

        return render_template("register.html")

    # --------------------------------------------------
    # FIRST ADMIN SETUP
    # --------------------------------------------------

    @app.route("/setup-admin", methods=["GET", "POST"])
    def setup_admin_page():
        admin_exists = User.query.filter_by(role="admin").first()

        if admin_exists:
            flash("An Admin account already exists.", "info")
            return redirect(url_for("login_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or len(username) > 80:
                flash("Enter a valid username.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif len(password) < 2:
                flash(
                    "Password must contain at least 2 characters.",
                    "danger",
                )
            elif password != confirm_password:
                flash("Passwords do not match.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                # Recheck before creating the first admin.
                if User.query.filter_by(role="admin").first():
                    flash("An Admin account already exists.", "danger")
                    return redirect(url_for("login_page"))

                user = User(
                    username=username,
                    full_name=full_name,
                    role="admin",
                    is_active=True,
                )
                user.set_password(password)

                try:
                    db.session.add(user)
                    db.session.commit()
                    flash(
                        "Admin account created. Please log in.",
                        "success",
                    )
                    return redirect(url_for("login_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Admin setup failed")
                    flash(
                        "Could not create the Admin account.",
                        "danger",
                    )

        return render_template("setup_admin.html")

    # --------------------------------------------------
    # CUSTOMERS: LIST, SEARCH AND DATE FILTERS
    # --------------------------------------------------

    @app.route("/customers")
    @login_required
    def customers_list():
        search = request.args.get("search", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        query = Customer.query

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Customer.name.ilike(pattern),
                    Customer.phone.ilike(pattern),
                    Customer.email.ilike(pattern),
                )
            )

        try:
            query = apply_date_filters(query, Customer, date_from, date_to)
        except ValueError:
            flash("Please enter valid filter dates.", "warning")
            return redirect(url_for("customers_list"))

        customers = query.order_by(Customer.id.desc()).all()

        return render_template(
            "customers/list.html",
            customers=customers,
            search=search,
            date_from=date_from,
            date_to=date_to,
            total_customers=Customer.query.count(),
            filtered_count=len(customers),
        )

    # --------------------------------------------------
    # CUSTOMERS: CREATE
    # --------------------------------------------------

    @app.route("/customers/create", methods=["GET", "POST"])
    @login_required
    def customer_create():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            address = request.form.get("address", "").strip()
            gst_number = request.form.get("gst_number", "").strip()

            if not name:
                flash("Customer name is required.", "danger")
                return render_template("customers/form.html", customer=None)

            customer = Customer(
                name=name,
                phone=phone or None,
                email=email or None,
                address=address or None,
                gst_number=gst_number or None,
            )

            try:
                db.session.add(customer)
                db.session.commit()
                flash("Customer created successfully.", "success")
                return redirect(url_for("customers_list"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Customer creation failed")
                flash("Could not save the customer.", "danger")

        return render_template("customers/form.html", customer=None)

    # --------------------------------------------------
    # CUSTOMERS: EDIT
    # --------------------------------------------------

    @app.route(
        "/customers/<int:customer_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def customer_edit(customer_id):
        customer = db.session.get(Customer, customer_id)

        if customer is None:
            flash("Customer not found.", "danger")
            return redirect(url_for("customers_list"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if not name:
                flash("Customer name is required.", "danger")
                return render_template("customers/form.html", customer=customer)

            customer.name = name
            customer.phone = request.form.get("phone", "").strip() or None
            customer.email = request.form.get("email", "").strip() or None
            customer.address = request.form.get("address", "").strip() or None
            customer.gst_number = request.form.get("gst_number", "").strip() or None

            try:
                db.session.commit()
                flash("Customer updated successfully.", "success")
                return redirect(url_for("customers_list"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Customer update failed")
                flash("Could not update the customer.", "danger")

        return render_template("customers/form.html", customer=customer)

    # --------------------------------------------------
    # PRODUCTS: LIST, SEARCH AND DATE FILTERS
    # --------------------------------------------------

    @app.route("/products")
    @login_required
    def products_list():
        search = request.args.get("search", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        query = Product.query

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Product.product_code.ilike(pattern),
                    Product.barcode.ilike(pattern),
                    Product.name.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                    Product.category.ilike(pattern),
                )
            )

        try:
            query = apply_date_filters(query, Product, date_from, date_to)
        except ValueError:
            flash("Please enter valid filter dates.", "warning")
            return redirect(url_for("products_list"))

        products = query.order_by(Product.id.desc()).all()

        return render_template(
            "products/list.html",
            products=products,
            search=search,
            date_from=date_from,
            date_to=date_to,
            total_products=Product.query.count(),
            filtered_count=len(products),
        )

    # --------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------

    @app.route("/dashboard")
    @login_required
    def dashboard_page():
        return render_template(
            "dashboard.html",
            user=current_user,
            total_customers=Customer.query.count(),
        )

    # --------------------------------------------------
    # LOGOUT
    # --------------------------------------------------

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout_page():
        logout_user()
        flash("You have logged out successfully.", "success")
        return redirect(url_for("login_page"))

    # --------------------------------------------------
    # ERROR HANDLERS
    # --------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        return "Page not found.", 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.exception("Internal server error")
        return "An internal server error occurred.", 500


        # --------------------------------------------------
    # SUPPLIER MANAGEMENT
    # --------------------------------------------------

    @app.route("/suppliers")
    @login_required
    def suppliers_list():
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "all").strip().lower()

        query = Supplier.query

        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    Supplier.name.ilike(term),
                    Supplier.company_name.ilike(term),
                    Supplier.contact_person.ilike(term),
                    Supplier.phone.ilike(term),
                    Supplier.mobile_number_2.ilike(term),
                    Supplier.email.ilike(term),
                    Supplier.gst_number.ilike(term),
                )
            )

        if status == "active":
            query = query.filter(Supplier.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Supplier.is_active.is_(False))

        suppliers = query.order_by(Supplier.name.asc()).all()

        return render_template(
            "suppliers/list.html",
            suppliers=suppliers,
            search=search,
            status=status,
            total_suppliers=Supplier.query.count(),
            active_suppliers=Supplier.query.filter_by(is_active=True).count(),
            inactive_suppliers=Supplier.query.filter_by(is_active=False).count(),
        )

    # --------------------------------------------------
    # SUPPLIER FORM HELPER
    # --------------------------------------------------

    def supplier_form_values():
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        mobile_number_2 = request.form.get("mobile_number_2", "").strip()
        email = request.form.get("email", "").strip()
        lead_time_text = request.form.get("delivery_lead_time", "0").strip()

        if not name:
            raise ValueError("Supplier name is required.")

        if len(name) > 150:
            raise ValueError("Supplier name cannot exceed 150 characters.")

        for label, number in (
            ("Mobile number 1", phone),
            ("Mobile number 2", mobile_number_2),
        ):
            if number and (not number.isdigit() or len(number) != 10):
                raise ValueError(f"{label} must contain exactly 10 digits.")

        if phone and mobile_number_2 and phone == mobile_number_2:
            raise ValueError("The two mobile numbers must be different.")

        if email and (
            "@" not in email
            or email.startswith("@")
            or email.endswith("@")
            or " " in email
        ):
            raise ValueError("Please enter a valid email address.")

        try:
            delivery_lead_time = int(lead_time_text or 0)
        except (ValueError, TypeError):
            raise ValueError("Delivery lead time must be a whole number.")

        if delivery_lead_time < 0:
            raise ValueError("Delivery lead time cannot be negative.")

        return {
            "name": name,
            "company_name": request.form.get("company_name", "").strip() or None,
            "contact_person": request.form.get("contact_person", "").strip() or None,
            "phone": phone or None,
            "mobile_number_2": mobile_number_2 or None,
            "email": email or None,
            "address": request.form.get("address", "").strip() or None,
            "gst_number": request.form.get("gst_number", "").strip().upper() or None,
            "supplier_category": request.form.get("supplier_category", "").strip() or None,
            "payment_terms": request.form.get("payment_terms", "").strip() or None,
            "delivery_lead_time": delivery_lead_time,
            "notes": request.form.get("notes", "").strip() or None,
        }

    # --------------------------------------------------
    # CREATE SUPPLIER
    # --------------------------------------------------

    @app.route("/suppliers/create", methods=["GET", "POST"])
    @login_required
    def supplier_create():
        if request.method == "POST":
            try:
                values = supplier_form_values()

                supplier = Supplier(
                    **values,
                    is_active=True,
                )

                db.session.add(supplier)
                db.session.commit()

                flash("Supplier added successfully.", "success")
                return redirect(url_for("suppliers_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                current_app.logger.exception("Supplier creation failed")
                flash("Unable to save the supplier.", "danger")

        return render_template("suppliers/form.html", supplier=None)

    # --------------------------------------------------
    # EDIT SUPPLIER
    # --------------------------------------------------

    @app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
    @login_required
    def supplier_edit(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        if request.method == "POST":
            try:
                values = supplier_form_values()

                for field, value in values.items():
                    setattr(supplier, field, value)

                db.session.commit()

                flash("Supplier updated successfully.", "success")
                return redirect(
                    url_for("supplier_detail", supplier_id=supplier.id)
                )

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                current_app.logger.exception("Supplier update failed")
                flash("Unable to update the supplier.", "danger")

        return render_template("suppliers/form.html", supplier=supplier)

    # --------------------------------------------------
    # SUPPLIER DETAILS
    # --------------------------------------------------

    @app.route("/suppliers/<int:supplier_id>")
    @login_required
    def supplier_detail(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        purchases = (
            Purchase.query.filter_by(supplier_id=supplier.id)
            .order_by(Purchase.purchase_date.desc())
            .all()
        )

        purchase_total = sum(
            float(purchase.total_amount or 0)
            for purchase in purchases
        )

        return render_template(
            "suppliers/detail.html",
            supplier=supplier,
            purchases=purchases,
            purchase_total=purchase_total,
        )

    # --------------------------------------------------
    # ACTIVATE / DEACTIVATE SUPPLIER
    # --------------------------------------------------

    @app.route(
        "/suppliers/<int:supplier_id>/toggle-status",
        methods=["POST"],
    )
    @login_required
    def supplier_toggle_status(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        supplier.is_active = not supplier.is_active

        try:
            db.session.commit()

            message = "activated" if supplier.is_active else "deactivated"
            flash(f"Supplier {message} successfully.", "success")

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Supplier status update failed")
            flash("Unable to change supplier status.", "danger")

        return redirect(url_for("suppliers_list"))

    return app


app = create_app()


def run_flask(flask_app):
    flask_app.run(
        host="127.0.0.1",
        port=5001,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    import webview

    flask_thread = threading.Thread(
        target=run_flask,
        args=(app,),
        daemon=True,
    )
    flask_thread.start()

    server_ready = False

    for _ in range(50):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:5001",
                timeout=1,
            ):
                server_ready = True
                break
        except Exception:
            time_module.sleep(0.2)

    if not server_ready:
        raise RuntimeError("Flask server failed to start on port 5001.")

    webview.create_window(
        "MobileStore ERP",
        "http://127.0.0.1:5001",
        width=1200,
        height=800,
    )

    webview.start()
