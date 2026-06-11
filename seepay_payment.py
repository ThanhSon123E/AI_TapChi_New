"""
Seepay Payment Integration Module
Handles payment processing with Seepay API
"""

import requests
import json
import hashlib
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_login import current_user, login_required
import os
from urllib.parse import urlencode

class SeepayPayment:
    def __init__(self):
        # Seepay API Configuration
        self.api_url = "https://api.seepay.vn"
        self.merchant_id = os.getenv('SEEPAY_MERCHANT_ID', 'YOUR_MERCHANT_ID')
        self.api_key = os.getenv('SEEPAY_API_KEY', 'YOUR_API_KEY')
        self.secret_key = os.getenv('SEEPAY_SECRET_KEY', 'YOUR_SECRET_KEY')
        self.callback_url = os.getenv('SEEPAY_CALLBACK_URL', 'https://yourdomain.com/seepay/callback')
        self.return_url = os.getenv('SEEPAY_RETURN_URL', 'https://yourdomain.com/recharge')
        
    def create_payment(self, amount, transaction_id, user_id, description="NAP TIEN AI MAGAZINE"):
        """
        Tao giao dich thanh toan Seepay
        """
        try:
            # Prepare payment data
            payment_data = {
                'merchant_id': self.merchant_id,
                'transaction_id': transaction_id,
                'amount': amount,
                'description': description,
                'user_id': user_id,
                'callback_url': self.callback_url,
                'return_url': self.return_url,
                'timestamp': int(time.time())
            }
            
            # Generate signature
            signature = self.generate_signature(payment_data)
            payment_data['signature'] = signature
            
            # Make API request
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}'
            }
            
            response = requests.post(
                f"{self.api_url}/payment/create",
                json=payment_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return {
                        'success': True,
                        'payment_url': result.get('payment_url'),
                        'transaction_id': transaction_id,
                        'amount': amount
                    }
                else:
                    return {
                        'success': False,
                        'message': result.get('message', 'Tao giao dich that bai')
                    }
            else:
                return {
                    'success': False,
                    'message': f'API Error: {response.status_code}'
                }
                
        except Exception as e:
            return {
                'success': False,
                'message': f'Loi ket noi: {str(e)}'
            }
    
    def generate_signature(self, data):
        """
        Tao chu ky ky thuat RSA
        """
        # Sort data by key
        sorted_data = sorted(data.items(), key=lambda x: x[0])
        
        # Create string to sign
        string_to_sign = '&'.join([f"{k}={v}" for k, v in sorted_data if k != 'signature'])
        
        # In real implementation, use RSA signature
        # For demo, we'll use simple hash
        signature = hashlib.sha256((string_to_sign + self.secret_key).encode()).hexdigest()
        
        return signature
    
    def verify_callback(self, callback_data):
        """
        Xac minh callback tu Seepay
        """
        try:
            # Extract signature
            received_signature = callback_data.get('signature')
            if not received_signature:
                return False, 'Thieu signature'
            
            # Generate expected signature
            expected_signature = self.generate_signature(callback_data)
            
            # Compare signatures
            if received_signature == expected_signature:
                return True, 'Xac minh thanh cong'
            else:
                return False, 'Signature khong hop le'
                
        except Exception as e:
            return False, f'Loi xac minh: {str(e)}'
    
    def check_payment_status(self, transaction_id):
        """
        Kiem tra trang thai thanh toan
        """
        try:
            check_data = {
                'merchant_id': self.merchant_id,
                'transaction_id': transaction_id,
                'timestamp': int(time.time())
            }
            
            signature = self.generate_signature(check_data)
            check_data['signature'] = signature
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}'
            }
            
            response = requests.post(
                f"{self.api_url}/payment/status",
                json=check_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return result
            else:
                return {'success': False, 'message': f'API Error: {response.status_code}'}
                
        except Exception as e:
            return {'success': False, 'message': f'Loi ket noi: {str(e)}'}

# Flask routes for payment handling
def setup_seepay_routes(app):
    """Cau hinh routes cho Seepay"""
    seepay = SeepayPayment()
    
    @app.route('/recharge')
    @login_required
    def recharge():
        """Trang nap tien - Redirect to billing"""
        return redirect(url_for('billing'))
    
    @app.route('/create-seepay-payment', methods=['POST'])
    @login_required
    def create_seepay_payment():
        """Tao thanh toan Seepay"""
        
        try:
            data = request.get_json()
            amount = data.get('amount')
            bonus = data.get('bonus', 0)
            method = data.get('method')
            
            if not amount or amount < 10000:
                return jsonify({'success': False, 'message': 'Sotien toi thieu 10,000 VND'})
            
            # Generate transaction ID
            transaction_id = f"TXN{int(time.time())}{current_user.id}"
            
            # Save transaction to database (Using app's Payment model)
            from app import db, Payment
            payment_code = f"MAG{transaction_id[-8:]}"
            new_payment = Payment(
                user_id=current_user.id,
                payment_code=payment_code,
                amount=amount,
                transfer_content=payment_code,
                status='pending'
            )
            db.session.add(new_payment)
            db.session.commit()
            
            if method == 'seepay':
                # Create Seepay payment
                result = seepay.create_payment(amount, transaction_id, current_user.id)
                return jsonify(result)
            else:
                # Handle other methods (bank transfer, momo, etc.)
                return handle_other_payment_methods(transaction_id, amount, bonus, method)
                
        except Exception as e:
            return jsonify({'success': False, 'message': f'Loi server: {str(e)}'})
    
    @app.route('/seepay/callback', methods=['POST'])
    def seepay_callback():
        """Callback tu Seepay"""
        try:
            callback_data = request.get_json()
            
            # Verify signature
            is_valid, message = seepay.verify_callback(callback_data)
            if not is_valid:
                return jsonify({'success': False, 'message': message})
            
            # Process payment
            transaction_id = callback_data.get('transaction_id')
            status = callback_data.get('status')
            amount = callback_data.get('amount')
            
            if status == 'success':
                # Update transaction status
                update_transaction_status(transaction_id, 'completed')
                
                # Update user balance
                user_id = get_user_id_by_transaction(transaction_id)
                if user_id:
                    transaction = get_transaction(transaction_id)
                    total_amount = transaction['amount'] + transaction['bonus']
                    update_user_balance(user_id, total_amount)
                
                return jsonify({'success': True, 'message': 'Callback processed'})
            else:
                update_transaction_status(transaction_id, 'failed')
                return jsonify({'success': True, 'message': 'Payment failed recorded'})
                
        except Exception as e:
            return jsonify({'success': False, 'message': f'Callback error: {str(e)}'})
    
    @app.route('/seepay/return')
    def seepay_return():
        """Return tu Seepay sau thanh toan"""
        transaction_id = request.args.get('transaction_id')
        status = request.args.get('status')
        
        if status == 'success':
            # Show success page
            return render_template('payment_success.html', transaction_id=transaction_id)
        else:
            # Show failure page
            return render_template('payment_failed.html', transaction_id=transaction_id)
    
    @app.route('/payment-history')
    def payment_history():
        """Lich su giao dich"""
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        transactions = get_user_transactions(session['user_id'])
        return render_template('payment_history.html', transactions=transactions)
    
    @app.route('/get-user-balance')
    def get_user_balance():
        """Lay so du user"""
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Chua dang nhap'})
        
        balance = get_balance(session['user_id'])
        return jsonify({'success': True, 'balance': balance})

# Helper functions (these would be implemented with your database)
def get_user_info(user_id):
    """Lay thong tin user"""
    # Implementation with your database
    pass

def save_transaction(transaction_data):
    """Luu giao dich vao database"""
    # Implementation with your database
    pass

def update_transaction_status(transaction_id, status):
    """Cap nhat trang thai giao dich"""
    # Implementation with your database
    pass

def get_user_id_by_transaction(transaction_id):
    """Lay user_id tu transaction_id"""
    # Implementation with your database
    pass

def get_transaction(transaction_id):
    """Lay thong tin giao dich"""
    # Implementation with your database
    pass

def update_user_balance(user_id, amount):
    """Cap nhat so du user"""
    # Implementation with your database
    pass

def get_user_transactions(user_id):
    """Lay lich su giao dich cua user"""
    # Implementation with your database
    pass

def get_balance(user_id):
    """Lay so du hien tai"""
    # Implementation with your database
    pass

def handle_other_payment_methods(transaction_id, amount, bonus, method):
    """Xu ly cac phuong thuc thanh toan khac"""
    if method == 'bank':
        # Return bank transfer information
        bank_info = {
            'bank_name': 'Vietcombank',
            'account_number': '1234567890',
            'account_holder': 'AI MAGAZINE GENERATOR',
            'amount': amount,
            'content': f'NAP {transaction_id}'
        }
        return jsonify({
            'success': True,
            'method': 'bank',
            'bank_info': bank_info,
            'transaction_id': transaction_id
        })
    elif method == 'momo':
        # Return MoMo payment information
        momo_info = {
            'phone': '0912345678',
            'amount': amount,
            'content': f'NAP {transaction_id}'
        }
        return jsonify({
            'success': True,
            'method': 'momo',
            'momo_info': momo_info,
            'transaction_id': transaction_id
        })
    elif method == 'zalopay':
        # Return ZaloPay payment information
        zalopay_info = {
            'phone': '0901234567',
            'amount': amount,
            'content': f'NAP {transaction_id}'
        }
        return jsonify({
            'success': True,
            'method': 'zalopay',
            'zalopay_info': zalopay_info,
            'transaction_id': transaction_id
        })
    else:
        return jsonify({'success': False, 'message': 'Phuong thuc thanh toan khong hop le'})

# Environment variables setup
def setup_seepay_env():
    """Cau hinh bien moi truong cho Seepay"""
    os.environ.setdefault('SEEPAY_MERCHANT_ID', 'YOUR_MERCHANT_ID')
    os.environ.setdefault('SEEPAY_API_KEY', 'YOUR_API_KEY')
    os.environ.setdefault('SEEPAY_SECRET_KEY', 'YOUR_SECRET_KEY')
    os.environ.setdefault('SEEPAY_CALLBACK_URL', 'http://localhost:5000/seepay/callback')
    os.environ.setdefault('SEEPAY_RETURN_URL', 'http://localhost:5000/recharge')

if __name__ == '__main__':
    setup_seepay_env()
