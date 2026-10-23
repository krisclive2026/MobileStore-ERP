import base64
import math
import os
import threading
import time as time_module
import urllib.request
from datetime import datetime, time, timedelta
from functools import wraps
from io import BytesIO

from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    flash,
    send_file,
    abort,
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
from werkzeug.exceptions import HTTPException
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from config import Config
from models import (
    db,
    User,
    Customer,
    Product,
    IMEIRecord,
    Supplier,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Payment,
    StockMovement,
    Expense,
    AuditLog,
)

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "login_page"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if (current_user.role or "").lower() != "admin":
                flash("Admin permission required.", "danger")
                return redirect(url_for("dashboard_page"))
            return view(*args, **kwargs)

        return wrapped

    def log_action(action, entity, entity_id=None, details=""):
        db.session.add(
            AuditLog(
                user_id=current_user.id if current_user.is_authenticated else None,
                action=action,
                entity=entity,
                entity_id=entity_id,
                details=details,
                ip_address=request.remote_addr,
            )
        )

    def completed_payments_total(sale_id):
        payments = Payment.query.filter_by(sale_id=sale_id).all()
        return round(
            sum(
                float(p.amount or 0)
                for p in payments
                if (p.status or "").lower() == "completed"
            ),
            2,
        )

    def pdf_response(title, headers, rows, filename):
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=24,
            leftMargin=24,
            topMargin=24,
            bottomMargin=24,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(title, styles["Title"]),
            Paragraph(
                "Generated on: " + datetime.now().strftime("%d-%m-%Y %H:%M"),
                styles["Normal"],
            ),
            Spacer(1, 14),
        ]
        data = [headers] + (rows or [["No records found"] + [""] * (len(headers) - 1)])
        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D3557")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F1F5F9")],
                    ),
                ]
            )
        )
        elements.append(table)
        document.build(elements)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    @app.route("/")
    def home():
        return redirect(
            url_for("dashboard_page")
            if current_user.is_authenticated
            else url_for("login_page")
        )

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

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout_page():
        logout_user()
        flash("You have logged out successfully.", "success")
        return redirect(url_for("login_page"))

    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif len(password) < 8:
                flash("Password must contain at least 8 characters.", "danger")
            elif password != confirm:
                flash("Passwords do not match.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                user = User(
                    username=username,
                    full_name=full_name,
                    role="cashier",
                    is_active=True,
                )
                user.set_password(password)
                try:
                    db.session.add(user)
                    db.session.commit()
                    flash("Staff account created. Please log in.", "success")
                    return redirect(url_for("login_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Registration failed")
                    flash("Could not create account.", "danger")
        return render_template("register.html")

    @app.route("/setup-admin", methods=["GET", "POST"])
    def setup_admin_page():
        if User.query.filter_by(role="admin").first():
            flash("An Admin account already exists.", "info")
            return redirect(url_for("login_page"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not username or not full_name:
                flash("Username and full name are required.", "danger")
            elif len(password) < 8:
                flash("Password must contain at least 8 characters.", "danger")
            elif password != confirm:
                flash("Passwords do not match.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                user = User(
                    username=username, full_name=full_name, role="admin", is_active=True
                )
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash("Admin account created. Please log in.", "success")
                return redirect(url_for("login_page"))
        return render_template("setup_admin.html")

    @app.route("/dashboard")
    @login_required
    def dashboard_page():
        today = datetime.now().date()
        start = datetime.combine(today, time.min)
        end = datetime.combine(today, time.max)
        total_products = Product.query.filter_by(is_active=True).count()
        total_customers = Customer.query.count()
        low_stock = Product.query.filter(
            Product.is_active.is_(True),
            Product.stock_quantity > 0,
            Product.stock_quantity <= Product.reorder_level,
        ).count()
        today_orders = Sale.query.filter(
            Sale.sale_date >= start, Sale.sale_date <= end
        ).count()
        today_sales = (
            db.session.query(func.coalesce(func.sum(Sale.total_amount), 0))
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .scalar()
        )
        paid_amount = (
            db.session.query(func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.status == "completed")
            .scalar()
        )
        recent_sales = Sale.query.order_by(Sale.id.desc()).limit(8).all()
        return render_template(
            "dashboard.html",
            user=current_user,
            total_products=total_products,
            total_customers=total_customers,
            low_stock=low_stock,
            today_orders=today_orders,
            today_sales=float(today_sales or 0),
            paid_amount=float(paid_amount or 0),
            recent_sales=recent_sales,
        )

    @app.route("/profile-settings", methods=["GET", "POST"])
    @login_required
    def profile_settings():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            duplicate = User.query.filter(
                User.username == username, User.id != current_user.id
            ).first()
            if not username or len(username) > 80 or not full_name:
                flash("Enter a valid username and full name.", "danger")
            elif duplicate:
                flash("That username is already in use.", "danger")
            elif password and len(password) < 8:
                flash("New password must contain at least 8 characters.", "danger")
            elif password and password != confirm:
                flash("Passwords do not match.", "danger")
            else:
                current_user.username = username
                current_user.full_name = full_name
                if password:
                    current_user.set_password(password)
                db.session.commit()
                flash("Profile updated successfully.", "success")
                return redirect(url_for("profile_settings"))
        return render_template("profile_settings.html", user=current_user)

    @app.route("/customers")
    @login_required
    def customers_list():
        search = request.args.get("search", "").strip()
        query = Customer.query
        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    Customer.name.ilike(term),
                    Customer.phone.ilike(term),
                    Customer.email.ilike(term),
                )
            )
        customers = query.order_by(Customer.id.desc()).all()
        return render_template(
            "customers/list.html",
            customers=customers,
            search=search,
            date_from="",
            date_to="",
            total_customers=Customer.query.count(),
            filtered_count=len(customers),
        )

    @app.route("/customers/create", methods=["GET", "POST"])
    @login_required
    def customer_create():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Customer name is required.", "danger")
            else:
                customer = Customer(
                    name=name,
                    phone=request.form.get("phone", "").strip() or None,
                    email=request.form.get("email", "").strip() or None,
                    address=request.form.get("address", "").strip() or None,
                    gst_number=request.form.get("gst_number", "").strip() or None,
                )
                db.session.add(customer)
                db.session.commit()
                flash("Customer created successfully.", "success")
                return redirect(url_for("customers_list"))
        return render_template("customers/form.html", customer=None)

    @app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
    @login_required
    def customer_edit(customer_id):
        customer = db.session.get(Customer, customer_id)
        if not customer:
            flash("Customer not found.", "danger")
            return redirect(url_for("customers_list"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Customer name is required.", "danger")
            else:
                customer.name = name
                customer.phone = request.form.get("phone", "").strip() or None
                customer.email = request.form.get("email", "").strip() or None
                customer.address = request.form.get("address", "").strip() or None
                customer.gst_number = request.form.get("gst_number", "").strip() or None
                db.session.commit()
                flash("Customer updated successfully.", "success")
                return redirect(url_for("customers_list"))
        return render_template("customers/form.html", customer=customer)

    def product_values():
        def number(name, default, converter):
            raw = request.form.get(name, "").strip()
            try:
                value = converter(raw) if raw else default
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Enter a valid {name.replace('_', ' ')}.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Enter a valid {name.replace('_', ' ')}.")
            return value

        code = request.form.get("product_code", "").strip()
        name = request.form.get("name", "").strip()
        if not code or len(code) > 50:
            raise ValueError("Product code is required (maximum 50 characters).")
        if not name or len(name) > 150:
            raise ValueError("Product name is required (maximum 150 characters).")
        vals = {
            "product_code": code,
            "barcode": request.form.get("barcode", "").strip() or None,
            "name": name,
            "brand": request.form.get("brand", "").strip() or None,
            "model": request.form.get("model", "").strip() or None,
            "category": request.form.get("category", "").strip() or "Mobile",
            "color": request.form.get("color", "").strip() or None,
            "ram": request.form.get("ram", "").strip() or None,
            "storage": request.form.get("storage", "").strip() or None,
            "hsn_code": request.form.get("hsn_code", "").strip() or None,
            "gst_percent": number("gst_percent", 0.0, float),
            "cost_price": number("cost_price", 0.0, float),
            "selling_price": number("selling_price", 0.0, float),
            "stock_quantity": number("stock_quantity", 0, int),
            "reorder_level": number("reorder_level", 5, int),
            "track_imei": request.form.get("track_imei") in ("on", "true", "1"),
            "is_active": request.form.get("is_active") in ("on", "true", "1"),
        }
        if any(
            vals[k] < 0
            for k in (
                "gst_percent",
                "cost_price",
                "selling_price",
                "stock_quantity",
                "reorder_level",
            )
        ):
            raise ValueError("Prices, GST, stock and reorder level cannot be negative.")
        if vals["gst_percent"] > 100:
            raise ValueError("GST cannot exceed 100%.")
        return vals

    @app.route("/products")
    @login_required
    def products_list():
        search = request.args.get("search", "").strip()
        query = Product.query
        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    Product.product_code.ilike(term),
                    Product.barcode.ilike(term),
                    Product.name.ilike(term),
                    Product.brand.ilike(term),
                    Product.model.ilike(term),
                    Product.category.ilike(term),
                )
            )
        products = query.order_by(Product.id.desc()).all()
        return render_template(
            "products/list.html",
            products=products,
            search=search,
            date_from="",
            date_to="",
            total_products=Product.query.count(),
            filtered_count=len(products),
        )

    @app.route("/products/create", methods=["GET", "POST"])
    @login_required
    def product_create():
        if request.method == "POST":
            try:
                values = product_values()
                if Product.query.filter_by(product_code=values["product_code"]).first():
                    raise ValueError("That product code already exists.")
                if (
                    values["barcode"]
                    and Product.query.filter_by(barcode=values["barcode"]).first()
                ):
                    raise ValueError("That barcode already exists.")
                product = Product(**values)
                db.session.add(product)
                db.session.flush()
                log_action("create", "Product", product.id, f"Created {product.name}")
                db.session.commit()
                flash("Product created successfully.", "success")
                return redirect(url_for("products_list"))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            except Exception:
                db.session.rollback()
                app.logger.exception("Product creation failed")
                flash("Could not save product. Check code/barcode.", "danger")
        return render_template("products/form.html", product=None)

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        product = db.session.get(Product, product_id)
        if not product:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))
        if request.method == "POST":
            try:
                values = product_values()
                if Product.query.filter(
                    Product.product_code == values["product_code"],
                    Product.id != product.id,
                ).first():
                    raise ValueError("That product code already exists.")
                if (
                    values["barcode"]
                    and Product.query.filter(
                        Product.barcode == values["barcode"], Product.id != product.id
                    ).first()
                ):
                    raise ValueError("That barcode already exists.")
                for key, value in values.items():
                    setattr(product, key, value)
                log_action("update", "Product", product.id, f"Updated {product.name}")
                db.session.commit()
                flash("Product updated successfully.", "success")
                return redirect(url_for("products_list"))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                product = db.session.get(Product, product_id)
            except Exception:
                db.session.rollback()
                app.logger.exception("Product update failed")
                flash("Could not update product.", "danger")
                product = db.session.get(Product, product_id)
        return render_template("products/form.html", product=product)

    @app.route("/inventory")
    @login_required
    def inventory():
        search = request.args.get("q", "").strip()
        status = request.args.get("status", "all").strip()
        query = Product.query
        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    Product.name.ilike(term),
                    Product.brand.ilike(term),
                    Product.model.ilike(term),
                    Product.product_code.ilike(term),
                    Product.barcode.ilike(term),
                )
            )
        if status == "low":
            query = query.filter(
                Product.is_active.is_(True),
                Product.stock_quantity > 0,
                Product.stock_quantity <= Product.reorder_level,
            )
        elif status == "out":
            query = query.filter(
                Product.is_active.is_(True), Product.stock_quantity <= 0
            )
        elif status == "inactive":
            query = query.filter(Product.is_active.is_(False))
        elif status == "in_stock":
            query = query.filter(
                Product.is_active.is_(True),
                Product.stock_quantity > Product.reorder_level,
            )
        products = query.order_by(Product.name.asc()).all()
        active = Product.query.filter_by(is_active=True).all()
        return render_template(
            "inventory/list.html",
            products=products,
            search=search,
            status=status,
            total_products=len(active),
            total_units=sum(max(0, p.stock_quantity or 0) for p in active),
            low_stock=sum(
                1
                for p in active
                if 0 < (p.stock_quantity or 0) <= (p.reorder_level or 0)
            ),
            out_of_stock=sum(1 for p in active if (p.stock_quantity or 0) <= 0),
            inventory_value=sum(
                (p.stock_quantity or 0) * (p.cost_price or 0) for p in active
            ),
        )

    @app.route("/inventory/adjust", methods=["POST"])
    @login_required
    def inventory_adjust():
        product_id = request.form.get("product_id", type=int)
        action = request.form.get("action", "").strip()
        quantity = request.form.get("quantity", type=int)
        reason = request.form.get("reason", "").strip()
        product = db.session.get(Product, product_id) if product_id else None
        if not product or not product.is_active:
            flash("Active product not found.", "danger")
            return redirect(url_for("inventory"))
        if (
            action not in ("add", "remove")
            or not quantity
            or quantity <= 0
            or not reason
        ):
            flash("Enter a valid action, quantity and reason.", "danger")
            return redirect(url_for("inventory"))
        change = quantity if action == "add" else -quantity
        new_qty = int(product.stock_quantity or 0) + change
        if new_qty < 0:
            flash("Insufficient stock for this adjustment.", "danger")
            return redirect(url_for("inventory"))
        product.stock_quantity = new_qty
        db.session.add(
            StockMovement(
                product_id=product.id,
                user_id=current_user.id,
                movement_type="adjustment_in" if change > 0 else "adjustment_out",
                quantity_change=change,
                reference=f"ADJ-{product.id}",
                notes=f"{reason} | Stock: {new_qty - change} -> {new_qty}",
            )
        )
        log_action(
            "inventory_adjustment",
            "Product",
            product.id,
            f"{product.name}: change {change}; reason={reason}",
        )
        db.session.commit()
        flash("Stock updated successfully.", "success")
        return redirect(url_for("inventory"))

    @app.route("/inventory/movements")
    @login_required
    def inventory_movements():
        movements = (
            StockMovement.query.order_by(StockMovement.id.desc()).limit(200).all()
        )
        return render_template("inventory/movements.html", movements=movements)

    @app.route("/billing")
    @login_required
    def billing_list():
        search = request.args.get("search", "").strip()
        query = Sale.query
        if search:
            term = f"%{search}%"
            query = query.outerjoin(Customer).filter(
                or_(
                    Sale.invoice_number.ilike(term),
                    Customer.name.ilike(term),
                    Customer.phone.ilike(term),
                )
            )
        sales = query.order_by(Sale.id.desc()).all()
        payment_totals = {sale.id: completed_payments_total(sale.id) for sale in sales}
        return render_template(
            "billing/list.html",
            sales=sales,
            search=search,
            payment_totals=payment_totals,
        )

    @app.route("/billing/create", methods=["GET", "POST"])
    @login_required
    def billing_create():
        customers = Customer.query.order_by(Customer.name).all()
        products = Product.query.filter_by(is_active=True).order_by(Product.name).all()
        if request.method == "GET":
            return render_template(
                "billing/form.html", customers=customers, products=products
            )
        try:
            customer = db.session.get(
                Customer, request.form.get("customer_id", type=int)
            )
            if not customer:
                raise ValueError("Please select a valid customer.")
            ids = request.form.getlist("product_id[]") or request.form.getlist(
                "product_id"
            )
            quantities = request.form.getlist("quantity[]") or request.form.getlist(
                "quantity"
            )
            if not ids or len(ids) != len(quantities):
                raise ValueError("Please add at least one valid product.")
            order = {}
            for raw_id, raw_qty in zip(ids, quantities):
                if raw_id and raw_qty:
                    pid, qty = int(raw_id), int(raw_qty)
                    if pid <= 0 or qty <= 0:
                        raise ValueError("Product and quantity must be positive.")
                    order[pid] = order.get(pid, 0) + qty
            if not order:
                raise ValueError("Please add at least one product.")
            products_map = {
                p.id: p for p in Product.query.filter(Product.id.in_(order)).all()
            }
            if set(products_map) != set(order):
                raise ValueError("A selected product no longer exists.")
            subtotal = tax_total = 0.0
            lines = []
            for pid, qty in order.items():
                p = products_map[pid]
                if not p.is_active or (p.stock_quantity or 0) < qty:
                    raise ValueError(
                        f"Insufficient stock or inactive product: {p.name}."
                    )
                price = float(p.selling_price or 0)
                gst = float(p.gst_percent or 0)
                if not math.isfinite(price) or price < 0 or not 0 <= gst <= 100:
                    raise ValueError(f"Invalid price/GST for {p.name}.")
                line = round(price * qty, 2)
                tax = round(line * gst / 100, 2)
                subtotal += line
                tax_total += tax
                lines.append((p, qty, price, line, tax))
            discount = float(request.form.get("discount_amount") or 0)
            if not math.isfinite(discount) or discount < 0:
                raise ValueError("Discount cannot be negative.")
            total = round(subtotal + tax_total - discount, 2)
            if total < 0:
                raise ValueError("Discount cannot exceed invoice total.")
            paid = float(request.form.get("paid_amount") or 0)
            if not math.isfinite(paid) or paid < 0 or paid > total:
                raise ValueError("Enter a valid paid amount not exceeding total.")
            method = request.form.get("payment_method", "cash").strip().lower()
            allowed = {"cash", "upi", "card", "bank_transfer", "other"}
            if paid and method not in allowed:
                raise ValueError("Select a valid payment method.")
            invoice = "INV-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            sale = Sale(
                invoice_number=invoice,
                customer_id=customer.id,
                user_id=current_user.id,
                sale_date=datetime.now(),
                subtotal=round(subtotal, 2),
                tax_amount=round(tax_total, 2),
                discount_amount=round(discount, 2),
                total_amount=total,
                status="completed",
            )
            db.session.add(sale)
            db.session.flush()
            for p, qty, price, line, tax in lines:
                db.session.add(
                    SaleItem(
                        sale_id=sale.id,
                        product_id=p.id,
                        quantity=qty,
                        unit_price=price,
                        subtotal=line,
                        tax_amount=tax,
                    )
                )
                p.stock_quantity -= qty
                db.session.add(
                    StockMovement(
                        product_id=p.id,
                        user_id=current_user.id,
                        movement_type="sale",
                        quantity_change=-qty,
                        reference=invoice,
                        notes=f"Stock deducted for invoice {invoice}",
                    )
                )
            if paid:
                db.session.add(
                    Payment(
                        sale_id=sale.id,
                        amount=round(paid, 2),
                        method=method,
                        reference=request.form.get("payment_reference", "").strip()
                        or None,
                        status="completed",
                    )
                )
            log_action("create", "Sale", sale.id, f"Created invoice {invoice}")
            db.session.commit()
            flash("Invoice created successfully.", "success")
            return redirect(url_for("billing_detail", sale_id=sale.id))
        except (ValueError, TypeError, OverflowError) as exc:
            db.session.rollback()
            flash(f"Please check invoice details: {exc}", "danger")
        except Exception:
            db.session.rollback()
            app.logger.exception("Invoice creation failed")
            flash("Could not create invoice. Check server log.", "danger")
        return render_template(
            "billing/form.html", customers=customers, products=products
        )

    @app.route("/billing/<int:sale_id>")
    @login_required
    def billing_detail(sale_id):
        sale = db.session.get(Sale, sale_id)
        if not sale:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))
        paid = completed_payments_total(sale.id)
        return render_template(
            "billing/detail.html",
            sale=sale,
            payments=sale.payments,
            paid_amount=paid,
            balance=round(float(sale.total_amount or 0) - paid, 2),
        )

    @app.route("/billing/due")
    @login_required
    def billing_due():
        due_sales = []
        for sale in Sale.query.order_by(Sale.id.desc()).all():
            paid = completed_payments_total(sale.id)
            balance = round(float(sale.total_amount or 0) - paid, 2)
            if balance > 0.009:
                due_sales.append({"sale": sale, "paid": paid, "balance": balance})
        return render_template("billing/due.html", due_sales=due_sales)

    @app.route("/billing/<int:sale_id>/receive-payment", methods=["GET", "POST"])
    @login_required
    def billing_receive_payment(sale_id):
        sale = db.session.get(Sale, sale_id)
        if not sale:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))
        if request.method == "POST":
            try:
                balance = round(
                    float(sale.total_amount or 0) - completed_payments_total(sale.id), 2
                )
                amount = round(float(request.form.get("amount", "")), 2)
                method = request.form.get("method", "cash").strip().lower()
                if balance <= 0.009:
                    raise ValueError("This invoice is already paid.")
                if not math.isfinite(amount) or amount <= 0 or amount > balance + 0.009:
                    raise ValueError(
                        "Enter a valid payment amount within the outstanding balance."
                    )
                if method not in {"cash", "upi", "card", "bank_transfer", "other"}:
                    raise ValueError("Select a valid payment method.")
                payment = Payment(
                    sale_id=sale.id,
                    amount=amount,
                    method=method,
                    reference=request.form.get("reference", "").strip() or None,
                    status="completed",
                )
                db.session.add(payment)
                db.session.flush()
                log_action(
                    "create",
                    "Payment",
                    payment.id,
                    f"Payment against {sale.invoice_number}: {amount:.2f}",
                )
                db.session.commit()
                flash("Payment received successfully.", "success")
                return redirect(url_for("billing_detail", sale_id=sale.id))
            except (ValueError, TypeError, OverflowError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
        paid = completed_payments_total(sale.id)
        balance = round(float(sale.total_amount or 0) - paid, 2)
        return render_template(
            "billing/Receive_pyment.html", sale=sale, paid_amount=paid, balance=balance
        )

    @app.route("/users")
    @admin_required
    def users_list():
        search = request.args.get("search", "").strip()
        role_filter = request.args.get("role", "").strip().lower()
        query = User.query
        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(User.username.ilike(term), User.full_name.ilike(term))
            )
        if role_filter:
            query = query.filter(func.lower(User.role) == role_filter)
        return render_template(
            "admin/Users.html",
            users=query.order_by(User.id.desc()).all(),
            search=search,
            role_filter=role_filter,
        )

    @app.route("/users/delete/<int:user_id>", methods=["POST"])
    @admin_required
    def delete_user(user_id):
        user = db.session.get(User, user_id)
        if not user:
            flash("User not found.", "danger")
        elif user.id == current_user.id:
            flash("You cannot delete your own account.", "warning")
        elif (user.role or "").lower() == "admin":
            flash("You cannot delete another admin account.", "danger")
        else:
            try:
                db.session.delete(user)
                db.session.commit()
                flash("User deleted successfully.", "success")
            except Exception:
                db.session.rollback()
                app.logger.exception("User deletion failed")
                flash("Unable to delete user; related records may exist.", "danger")
        return redirect(url_for("users_list"))

    @app.route("/analytics")
    @admin_required
    def analytics():
        sales = Sale.query.order_by(Sale.id.desc()).all()
        total_sales = sum(float(s.total_amount or 0) for s in sales)
        return render_template(
            "admin/Analytics.html",
            sales_rows=sales,
            profit_rows=[],
            total_sales=total_sales,
            total_profit=0,
            transaction_count=len(sales),
            average_sale=(total_sales / len(sales) if sales else 0),
            start_date=request.args.get("start_date", ""),
            end_date=request.args.get("end_date", ""),
        )

    @app.route("/about")
    @login_required
    def about():
        return render_template("about.html")

    @app.route("/reports")
    @login_required
    def reports_page():
        return render_template(
            "reports.html",
            start_date=request.args.get("start_date", "").strip(),
            end_date=request.args.get("end_date", "").strip(),
        )

    @app.route("/reports/products/pdf")
    @login_required
    def products_pdf():
        products = Product.query.order_by(Product.name).all()
        rows = [
            [
                p.product_code or "",
                p.name or "",
                p.brand or "",
                p.stock_quantity or 0,
                f"{float(p.selling_price or 0):.2f}",
            ]
            for p in products
        ]
        return pdf_response(
            "Product Inventory Report",
            ["Product Code", "Product", "Brand", "Stock", "Selling Price"],
            rows,
            "product_inventory.pdf",
        )

    @app.route("/reports/pdf/<report_type>")
    @login_required
    def report_pdf(report_type):
        kind = report_type.lower().strip()
        if kind in ("inventory", "products"):
            products = Product.query.order_by(Product.name).all()
            return pdf_response(
                "Inventory Report",
                ["Code", "Product", "Brand", "Stock", "Selling Price"],
                [
                    [
                        p.product_code or "",
                        p.name or "",
                        p.brand or "",
                        p.stock_quantity or 0,
                        f"{float(p.selling_price or 0):.2f}",
                    ]
                    for p in products
                ],
                "inventory_report.pdf",
            )
        if kind == "sales":
            start_date = request.args.get("start_date", "").strip()
            end_date = request.args.get("end_date", "").strip()
            query = Sale.query

            start = None
            end_exclusive = None
            try:
                if start_date:
                    start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
                    start = datetime.combine(start_day, time.min)
                if end_date:
                    end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
                    end_exclusive = datetime.combine(
                        end_day + timedelta(days=1), time.min
                    )
            except ValueError:
                abort(400, description="Please select valid start and end dates.")

            if start_date and end_date and start_date > end_date:
                abort(400, description="Start date cannot be after end date.")
            if start is not None:
                query = query.filter(Sale.sale_date >= start)
            if end_exclusive is not None:
                query = query.filter(Sale.sale_date < end_exclusive)

            sales = query.order_by(Sale.sale_date.desc(), Sale.id.desc()).all()
            filename = "sales_report.pdf"
            if start_date or end_date:
                filename = f"sales_report_{start_date or 'beginning'}_to_{end_date or 'today'}.pdf"

            return pdf_response(
                "Sales Report",
                [
                    "Invoice",
                    "Date",
                    "Customer",
                    "Subtotal",
                    "Tax",
                    "Discount",
                    "Total",
                    "Status",
                ],
                [
                    [
                        s.invoice_number or "",
                        s.sale_date.strftime("%d-%m-%Y") if s.sale_date else "",
                        s.customer.name if s.customer else "Walk-in",
                        f"{float(s.subtotal or 0):.2f}",
                        f"{float(s.tax_amount or 0):.2f}",
                        f"{float(s.discount_amount or 0):.2f}",
                        f"{float(s.total_amount or 0):.2f}",
                        s.status or "",
                    ]
                    for s in sales
                ],
                filename,
            )
        if kind == "purchases":
            purchases = Purchase.query.order_by(Purchase.id.desc()).all()
            return pdf_response(
                "Purchases Report",
                ["Invoice", "Date", "Supplier", "Total", "Status"],
                [
                    [
                        p.invoice_number or "",
                        p.purchase_date.strftime("%d-%m-%Y") if p.purchase_date else "",
                        p.supplier.name if p.supplier else "",
                        f"{p.total_amount or 0:.2f}",
                        p.status or "",
                    ]
                    for p in purchases
                ],
                "purchases_report.pdf",
            )
        if kind == "customers":
            customers = Customer.query.order_by(Customer.name).all()
            return pdf_response(
                "Customers Report",
                ["Name", "Phone", "Email", "GST Number"],
                [
                    [c.name or "", c.phone or "", c.email or "", c.gst_number or ""]
                    for c in customers
                ],
                "customers_report.pdf",
            )
        if kind == "expenses":
            expenses = Expense.query.order_by(Expense.expense_date.desc()).all()
            return pdf_response(
                "Expenses Report",
                ["Title", "Category", "Date", "Amount", "Notes"],
                [
                    [
                        e.title or "",
                        e.category or "",
                        e.expense_date.strftime("%d-%m-%Y") if e.expense_date else "",
                        f"{e.amount or 0:.2f}",
                        e.notes or "",
                    ]
                    for e in expenses
                ],
                "expenses_report.pdf",
            )
        if kind == "imei":
            records = IMEIRecord.query.order_by(IMEIRecord.id.desc()).all()
            return pdf_response(
                "IMEI Report",
                ["IMEI", "Product Code", "Product", "Status", "Sale ID"],
                [
                    [
                        r.imei or "",
                        r.product.product_code if r.product else "",
                        r.product.name if r.product else "",
                        r.status or "",
                        r.sale_id or "",
                    ]
                    for r in records
                ],
                "imei_report.pdf",
            )
        abort(404, description="The requested report does not exist.")

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/reports/pdf/"):
            return "Requested report not found.", 404
        return "Page not found.", 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.exception("Internal server error")
        return "An internal server error occurred.", 500

    
    return app


app = create_app()


class DesktopAPI:
    """Bridge used by pywebview to save PDF bytes through a Windows save dialog."""

    def save_pdf(self, pdf_base64, filename="report.pdf"):
        try:
            import webview
        except ImportError:
            return {"saved": False, "error": "pywebview is not installed."}

        if not isinstance(pdf_base64, str) or not pdf_base64:
            return {"saved": False, "error": "No PDF data was received."}

        safe_name = os.path.basename(str(filename or "report.pdf"))
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"

        try:
            pdf_bytes = base64.b64decode(pdf_base64, validate=True)
            if not pdf_bytes.startswith(b"%PDF"):
                return {"saved": False, "error": "The response is not a valid PDF."}

            window = webview.windows[0]
            path = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=safe_name,
                file_types=("PDF Files (*.pdf)",),
            )
            if not path:
                return {"saved": False, "cancelled": True}
            if isinstance(path, (list, tuple)):
                path = path[0] if path else None
            if not path:
                return {"saved": False, "cancelled": True}
            path = os.fspath(path)
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            with open(path, "wb") as output_file:
                output_file.write(pdf_bytes)
            return {"saved": True, "path": path}
        except Exception as exc:
            return {"saved": False, "error": str(exc)}


def run_flask(flask_app):
    flask_app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        import webview
    except ImportError:
        run_flask(app)
    else:
        flask_thread = threading.Thread(target=run_flask, args=(app,), daemon=True)
        flask_thread.start()
        ready = False
        last_error = None
        for _ in range(50):
            try:
                with urllib.request.urlopen("http://127.0.0.1:5001/login", timeout=1):
                    ready = True
                    break
            except Exception as exc:
                last_error = exc
                time_module.sleep(0.2)
        if not ready:
            raise RuntimeError(
                f"Flask server failed to start on port 5001: {last_error}"
            )
        api = DesktopAPI()
        webview.create_window(
            "MobileStore ERP",
            "http://127.0.0.1:5001",
            width=1200,
            height=800,
            js_api=api,
        )
        webview.start(debug=False)
