import {useEffect, useState} from 'react';
import {listPersons, type PersonPublic} from '../api/registry';

export function PersonPicker({name = 'person_id', value, onChange, label = 'Hồ sơ sinh viên'}: {name?: string; value?: string; onChange?: (id: string) => void; label?: string}) {
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<PersonPublic[]>([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<PersonPublic | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listPersons(query, 0, controller.signal).then(page => {if (!controller.signal.aborted) {setRows(page.items); setError('');}}).catch(() => {if (!controller.signal.aborted) setError('Không tải được hồ sơ. Thử tìm lại.');});
    }, 250);
    return () => {controller.abort(); clearTimeout(timer);};
  }, [query]);
  const options = selected && !rows.some(person => person.id === selected.id) ? [selected, ...rows] : rows;
  return <div className="person-picker"><label>Tìm theo tên hoặc mã sinh viên<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Ví dụ: Nguyễn An hoặc HE…"/></label><label>{label}<select name={name} value={value} onChange={e => {setSelected(options.find(person => person.id === e.target.value) ?? null); onChange?.(e.target.value);}}><option value="">Chưa chọn hồ sơ</option>{options.map(person => <option key={person.id} value={person.id}>{person.full_name} · {person.student_id ?? `#${person.person_number}`}</option>)}</select></label><small className="field-hint">Hiển thị tối đa 20 kết quả. Nhập thêm để thu hẹp tìm kiếm.</small>{error && <p role="alert">{error}</p>}</div>;
}
