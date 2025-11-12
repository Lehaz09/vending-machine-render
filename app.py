from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-2024')

# Database configuration for Render
if 'DATABASE_URL' in os.environ:
    # Render PostgreSQL
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL'].replace("postgres://", "postgresql://", 1)
    print("Using Render PostgreSQL database")
else:
    # SQLite for development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///vending_machine.db'
    print("Using local SQLite database")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Database Models
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

class TransactionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(20), nullable=False)
    amount_inserted_notes = db.Column(db.Text, nullable=False)
    amount_inserted_coins = db.Column(db.Text, nullable=False)
    change_returned_notes = db.Column(db.Text, nullable=False)
    change_returned_coins = db.Column(db.Text, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    change_amount = db.Column(db.Float, nullable=False)
    products_purchased = db.Column(db.Text, nullable=True)

# Initialize database
@app.before_first_request
def create_tables():
    db.create_all()
    # Add sample products if empty
    if Product.query.count() == 0:
        sample_products = [
            Product(id=1, name='sando', type='cake', price=15, quantity=10),
            Product(id=2, name='lays', type='cake', price=20, quantity=8),
            Product(id=3, name='m&m', type='cake', price=30, quantity=5),
            Product(id=4, name='Coca Cola', type='drink', price=50, quantity=15),
            Product(id=5, name='Sprite', type='drink', price=45, quantity=12),
            Product(id=6, name='water', type='drink', price=25, quantity=10)
        ]
        db.session.bulk_save_objects(sample_products)
        db.session.commit()
        print("Sample products added to database")

# Denominations
NOTES = [100, 50, 25, 20]
COINS = [10, 5, 1, 0.50, 0.25, 0.10, 0.05]

# Routes
@app.route('/')
def index():
    cakes = Product.query.filter_by(type='cake').order_by(Product.id).all()
    drinks = Product.query.filter_by(type='drink').order_by(Product.id).all()
    
    # Initialize session if not exists
    if 'inserted_money' not in session:
        session['inserted_money'] = 0.0
        session['denominations_inserted'] = {str(denom): 0 for denom in NOTES + COINS}
        session['current_transaction'] = []
    
    return render_template('index.html', 
                         cakes=cakes, 
                         drinks=drinks, 
                         inserted_money=session['inserted_money'],
                         notes=NOTES,
                         coins=COINS)

@app.route('/insert_money', methods=['POST'])
def insert_money():
    amount = float(request.form['amount'])
    session['inserted_money'] += amount
    
    # Update denominations
    denom_key = str(amount)
    if denom_key in session['denominations_inserted']:
        session['denominations_inserted'][denom_key] += 1
    
    session.modified = True
    
    return jsonify({
        'inserted_money': round(session['inserted_money'], 2),
        'message': f'Inserted: Rs {amount:.2f}'
    })

@app.route('/purchase', methods=['POST'])
def purchase():
    try:
        product_id = int(request.form['product_id'])
        quantity = int(request.form['quantity'])
    except ValueError:
        return jsonify({'error': 'Please enter valid product ID and quantity'}), 400
    
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'error': 'Invalid product ID'}), 400
    
    if product.quantity < quantity:
        return jsonify({'error': f'Insufficient quantity. Only {product.quantity} available'}), 400
    
    total_cost = product.price * quantity
    
    if session['inserted_money'] < total_cost:
        return jsonify({'error': f'Insufficient funds. Required: Rs {total_cost:.2f}'}), 400
    
    # Process purchase
    product.quantity -= quantity
    session['inserted_money'] -= total_cost
    
    # Add to current transaction
    purchase_item = {
        'product_id': product_id,
        'name': product.name,
        'quantity': quantity,
        'price': product.price,
        'total': total_cost
    }
    session['current_transaction'].append(purchase_item)
    
    db.session.commit()
    session.modified = True
    
    return jsonify({
        'success': f'Purchase successful! Purchased {quantity} x {product.name}. Remaining balance: Rs {session["inserted_money"]:.2f}',
        'inserted_money': round(session['inserted_money'], 2),
        'remaining_quantity': product.quantity
    })

@app.route('/return_change', methods=['POST'])
def return_change():
    if session['inserted_money'] > 0:
        change_breakdown = calculate_change(session['inserted_money'])
        change_message = format_change_message(change_breakdown)
        
        # Log transaction
        log_transaction(change_breakdown)
        
        change_amount = session['inserted_money']
        
        # Reset session
        session['inserted_money'] = 0.0
        session['denominations_inserted'] = {str(denom): 0 for denom in NOTES + COINS}
        session['current_transaction'] = []
        session.modified = True
        
        return jsonify({
            'success': f'Change returned: Rs {change_amount:.2f}',
            'change_breakdown': change_message,
            'inserted_money': 0.0
        })
    else:
        # Still log the transaction even if no change
        if session['current_transaction']:
            log_transaction({})
        
        session['inserted_money'] = 0.0
        session['denominations_inserted'] = {str(denom): 0 for denom in NOTES + COINS}
        session['current_transaction'] = []
        session.modified = True
        
        return jsonify({'info': 'Thank you for your purchase!'})

@app.route('/admin')
def admin():
    transactions = TransactionLog.query.order_by(TransactionLog.date.desc(), TransactionLog.time.desc()).limit(50).all()
    products = Product.query.order_by(Product.id).all()
    return render_template('admin.html', transactions=transactions, products=products)

@app.route('/admin/update_product', methods=['POST'])
def update_product():
    try:
        product_id = int(request.form['product_id'])
        name = request.form['name']
        product_type = request.form['type']
        price = float(request.form['price'])
        quantity = int(request.form['quantity'])
    except ValueError:
        flash('Please enter valid values for all fields', 'error')
        return redirect(url_for('admin'))
    
    product = Product.query.get(product_id)
    if product:
        product.name = name
        product.type = product_type
        product.price = price
        product.quantity = quantity
        db.session.commit()
        flash('Product updated successfully!', 'success')
    else:
        flash('Product not found!', 'error')
    
    return redirect(url_for('admin'))

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    try:
        name = request.form['new_name']
        product_type = request.form['new_type']
        price = float(request.form['new_price'])
        quantity = int(request.form['new_quantity'])
    except ValueError:
        flash('Please enter valid values for all fields', 'error')
        return redirect(url_for('admin'))
    
    # Get next available ID
    max_id = db.session.query(db.func.max(Product.id)).scalar()
    new_id = (max_id or 0) + 1
    
    new_product = Product(
        id=new_id,
        name=name,
        type=product_type,
        price=price,
        quantity=quantity
    )
    
    db.session.add(new_product)
    db.session.commit()
    flash(f'Product added successfully with ID: {new_id}', 'success')
    
    return redirect(url_for('admin'))

@app.route('/admin/delete_product/<int:product_id>')
def delete_product(product_id):
    product = Product.query.get(product_id)
    if product:
        db.session.delete(product)
        db.session.commit()
        flash('Product deleted successfully!', 'success')
    else:
        flash('Product not found!', 'error')
    
    return redirect(url_for('admin'))

@app.route('/database_info')
def database_info():
    info = {
        'database_uri': 'Render PostgreSQL' if 'DATABASE_URL' in os.environ else 'Local SQLite',
        'total_products': Product.query.count(),
        'total_transactions': TransactionLog.query.count(),
        'hosting_platform': 'Render',
        'database_connected': True
    }
    return jsonify(info)

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'platform': 'Render'})

# Helper functions
def calculate_change(amount):
    change_breakdown = {}
    remaining = amount
    
    for denom in sorted(NOTES + COINS, reverse=True):
        if remaining >= denom:
            count = int(remaining / denom)
            change_breakdown[denom] = count
            remaining = round(remaining - (count * denom), 2)
    
    return change_breakdown

def format_change_message(change_breakdown):
    message = "Change breakdown:\n"
    notes = []
    coins = []
    
    for denom, count in change_breakdown.items():
        if denom >= 1:
            notes.append(f"Rs {denom}: {count} note(s)")
        else:
            coins.append(f"Rs {denom:.2f}: {count} coin(s)")
    
    if notes:
        message += "Notes: " + ", ".join(notes) + "\n"
    if coins:
        message += "Coins: " + ", ".join(coins)
    
    return message

def log_transaction(change_breakdown):
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")
    
    # Format inserted denominations
    inserted_notes = {k: v for k, v in session['denominations_inserted'].items() if float(k) >= 1 and v > 0}
    inserted_coins = {k: v for k, v in session['denominations_inserted'].items() if float(k) < 1 and v > 0}
    
    # Format change denominations
    change_notes = {k: v for k, v in change_breakdown.items() if k >= 1 and v > 0}
    change_coins = {k: v for k, v in change_breakdown.items() if k < 1 and v > 0}
    
    # Calculate total inserted amount
    total_inserted = sum(float(denom) * count for denom, count in session['denominations_inserted'].items())
    
    # Format products purchased
    products_purchased = ", ".join([f"{item['quantity']}x {item['name']}" for item in session['current_transaction']])
    
    transaction = TransactionLog(
        date=date,
        time=time,
        amount_inserted_notes=str(inserted_notes),
        amount_inserted_coins=str(inserted_coins),
        change_returned_notes=str(change_notes),
        change_returned_coins=str(change_coins),
        total_amount=total_inserted,
        change_amount=session['inserted_money'],
        products_purchased=products_purchased
    )
    
    db.session.add(transaction)
    db.session.commit()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)