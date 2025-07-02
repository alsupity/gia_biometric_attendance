# -*- coding: utf-8 -*-
import datetime
import logging
import time
import pytz
import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BiometricDeviceDetails(models.Model):
    _name = 'biometric.device.details'
    _description = 'Biometric Device Details'

    name = fields.Char(string='Name', required=True)
    device_ip = fields.Char(string='Device IP', required=True)
    port_number = fields.Integer(string='Port Number', required=True)
    username = fields.Char(string='Username', required=True)
    password = fields.Char(string='Password', required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.user.company_id.id
    )
    last_download_time = fields.Datetime(string='Last Download Time')
    _session_cache = {}

    def get_api_session(self, retry_attempts=5):
        """إعادة استخدام جلسة المصادقة وتخزينها في ذاكرة التخزين المؤقت"""
        if self.id in self._session_cache:
            return self._session_cache[self.id]

        session = requests.Session()
        login_url = f"http://{self.device_ip}:{self.port_number}/v1/login"
        login_payload = {
            "userId": self.username,
            "password": self.password,
            "userType": 2
        }

        for attempt in range(retry_attempts):
            try:
                response = session.post(login_url, json=login_payload)
                if response.status_code == 200 and response.json().get("Result", {}).get("ResultCode") == 0:
                    self._session_cache[self.id] = session
                    return session
                else:
                    _logger.warning(f"Failed login attempt {attempt + 1}")
            except requests.RequestException as e:
                _logger.error(f"Error during login attempt {attempt + 1}: {e}")
                wait_time = 2 * (attempt + 1)  # الانتظار وقتًا أطول في كل محاولة
                time.sleep(wait_time)

        raise UserError(_("Unable to authenticate after multiple attempts."))

    def action_download_attendance(self):
        """
        تنزيل سجلات الحضور بكفاءة مع تحسين التصفية والمعالجة
        """
        hr_attendance = self.env['hr.attendance']
        gia_biometric_attendance = self.env['gia.biometric.attendance']
        session = self.get_api_session()

        api_url = f"http://{self.device_ip}:{self.port_number}/v1/authLogs"

        # حساب وقت البدء ديناميكيًا بناءً على آخر تنزيل أو افتراضيًا إلى بداية اليوم
        if self.last_download_time:
            start_time = fields.Datetime.to_string(self.last_download_time)
        else:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            start_time = f"{today} 00:00:00"

        end_time = f"{datetime.datetime.now().strftime('%Y-%m-%d')} 23:59:59"

        params = {
            "startTime": start_time,
            "endTime": end_time,
            "searchCategory": "all",
            "searchKeyword": "",
            "offset": 0,
            "limit": 10000
        }

        for attempt in range(5):
            try:
                response = session.get(api_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    employee_attendance = {}
                    employee_last_punch = {}

                    for record in data.get('AuthLogList', []):
                        user_id = record['UserID']
                        event_time = record['EventTime']

                        local_tz = pytz.timezone(self.env.user.partner_id.tz or 'GMT')
                        local_dt = local_tz.localize(
                            datetime.datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S"),
                            is_dst=None
                        )
                        utc_dt = local_dt.astimezone(pytz.utc)
                        punching_time_str = fields.Datetime.to_string(utc_dt)

                        # تقريب وقت البصمة إلى بداية الدقيقة
                        punching_time_minute = utc_dt.replace(second=0, microsecond=0)

                        employee = self.env['hr.employee'].search([('device_id_num', '=', user_id)], limit=1)
                        if not employee:
                            continue

                        last_punch_time = employee_last_punch.get(employee.id)
                        if last_punch_time and last_punch_time == punching_time_minute:
                            # تم تسجيل بصمة في نفس الدقيقة، نتجاوز
                            continue

                        # تحديث آخر بصمة للموظف
                        employee_last_punch[employee.id] = punching_time_minute

                        # التحقق إذا كان سجل البصمة موجودًا بالفعل
                        existing_biometric_record = gia_biometric_attendance.search([
                            ('employee_id', '=', employee.id),
                            ('punching_time', '>=', punching_time_minute),
                            ('punching_time', '<', punching_time_minute + datetime.timedelta(minutes=1))
                        ], limit=1)

                        # إنشاء سجل البصمة فقط إذا لم يوجد سجل في نفس الدقيقة
                        if not existing_biometric_record:
                            gia_biometric_attendance.create({
                                'employee_id': employee.id,
                                'device_id_num': user_id,
                                'punching_time': punching_time_str,
                            })

                        date_key = utc_dt.date()
                        if user_id not in employee_attendance:
                            employee_attendance[user_id] = {date_key: {'first': utc_dt, 'last': utc_dt}}
                        elif date_key not in employee_attendance[user_id]:
                            employee_attendance[user_id][date_key] = {'first': utc_dt, 'last': utc_dt}
                        else:
                            # تحديث أوقات الدخول والخروج
                            if utc_dt < employee_attendance[user_id][date_key]['first']:
                                employee_attendance[user_id][date_key]['first'] = utc_dt
                            if utc_dt > employee_attendance[user_id][date_key]['last']:
                                employee_attendance[user_id][date_key]['last'] = utc_dt

                    # معالجة الحضور فقط للأوقات المكتملة للدخول والخروج
                    for user_id, dates in employee_attendance.items():
                        employee = self.env['hr.employee'].search([('device_id_num', '=', user_id)], limit=1)
                        if not employee:
                            continue

                        for date_key, times in dates.items():
                            check_in_time = times['first']
                            check_out_time = times['last']

                            # إنشاء الحضور فقط إذا كانت أوقات الدخول والخروج مختلفة
                            if check_in_time != check_out_time:
                                check_in_time_str = fields.Datetime.to_string(check_in_time)
                                check_out_time_str = fields.Datetime.to_string(check_out_time)

                                # التحقق إذا كان سجل الحضور موجودًا بالفعل لليوم
                                existing_attendance = hr_attendance.search([
                                    ('employee_id', '=', employee.id),
                                    ('check_in', '>=', f"{date_key} 00:00:00"),
                                    ('check_in', '<=', f"{date_key} 23:59:59")
                                ], limit=1)

                                if existing_attendance:
                                    # تحديث سجل الحضور الحالي بوقت الخروج الجديد
                                    try:
                                        existing_attendance.write({
                                            'check_out': check_out_time_str
                                        })
                                    except Exception as e:
                                        _logger.error(f"Error updating attendance for employee {employee.name}: {e}")
                                else:
                                    # إنشاء سجل حضور جديد
                                    try:
                                        hr_attendance.create({
                                            'employee_id': employee.id,
                                            'check_in': check_in_time_str,
                                            'check_out': check_out_time_str
                                        })
                                    except Exception as e:
                                        _logger.error(f"Error creating attendance for employee {employee.name}: {e}")

                    # تحديث وقت آخر تنزيل
                    self.last_download_time = fields.Datetime.now()
                    break
                else:
                    _logger.error(f"API response error. Status code: {response.status_code}")
            except requests.RequestException as e:
                _logger.error(f"Error during attendance fetch attempt {attempt + 1}: {e}")
                wait_time = 2 * (attempt + 1)
                time.sleep(wait_time)

    @api.model
    def cron_download_attendance(self):
        """
        وظيفة مجدولة لتنزيل الحضور لجميع أجهزة البصمة المُعَدَّة
        """
        devices = self.search([])
        for device in devices:
            try:
                device.action_download_attendance()
            except Exception as e:
                _logger.error(f"Error in cron job for device {device.name}: {e}")
