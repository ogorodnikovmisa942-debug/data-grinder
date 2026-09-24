import unittest
from fastapi.testclient import TestClient
from main import app
from app.core.config import settings


class TestAdminAllUsers(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = settings.ADMIN_TOKEN
        self.headers = {'X-Admin-Token': self.token}

    def test_01_get_users_unauthorized(self):
        r = self.client.get('/api/admin/users')
        self.assertEqual(r.status_code, 403)

    def test_02_get_users_with_header(self):
        r = self.client.get('/api/admin/users', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('users', data)
        self.assertIn('total_all', data)
        self.assertIn('total_participants', data)
        self.assertGreaterEqual(data['total_all'], 1)

    def test_03_get_users_with_query_token(self):
        r = self.client.get(f'/api/admin/users?token={self.token}')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['status'], 'success')

    def test_04_export_users_csv(self):
        r = self.client.get(f'/api/admin/export/users?token={self.token}')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r.headers.get('content-type', ''))
        content = r.content.decode('utf-8-sig')
        self.assertIn('telegram_id', content)
        self.assertIn('user_id', content)
        self.assertIn('is_experiment_participant', content)

    def test_05_export_experiment_dataset_all_users(self):
        r = self.client.get(f'/api/admin/export/experiment-dataset?token={self.token}&all_users=true')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r.headers.get('content-type', ''))

    def test_06_admin_web_page_renders_stats(self):
        r = self.client.get('/admin')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Пользователи', r.text)
        self.assertIn('В эксперименте', r.text)


if __name__ == '__main__':
    unittest.main()
